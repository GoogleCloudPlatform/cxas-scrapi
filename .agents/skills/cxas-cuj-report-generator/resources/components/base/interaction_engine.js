// Dark Mode Support
document.addEventListener('DOMContentLoaded', () => {
  const themeToggle = document.getElementById('themeToggle');
  const themeToggleIcon = document.getElementById('themeToggleIcon');
  const themeToggleText = document.getElementById('themeToggleText');

  if (!themeToggle) {
    console.warn('themeToggle button not found!');
    return;
  }

  let currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';

  function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem('theme', theme);
    } catch (e) {
      console.warn('localStorage not available:', e);
    }
    currentTheme = theme;
    
    if (theme === 'dark') {
      themeToggleIcon.textContent = '☀️';
      themeToggleText.textContent = 'Light Mode';
    } else {
      themeToggleIcon.textContent = '🌙';
      themeToggleText.textContent = 'Dark Mode';
    }
  }

  // Initialize button text based on current theme
  if (currentTheme === 'dark') {
    themeToggleIcon.textContent = '☀️';
    themeToggleText.textContent = 'Light Mode';
  } else {
    themeToggleIcon.textContent = '🌙';
    themeToggleText.textContent = 'Dark Mode';
  }

  themeToggle.addEventListener('click', () => {
    setTheme(currentTheme === 'light' ? 'dark' : 'light');
  });
});

let currentSelectionData = null;

// Persistent Storage for Notes (in-memory, exported with HTML)
let notesData = []; // Array of {id, rowId, start, end, text, originalText}

const escapeHtml = (str) => {
  if (!str) return '';
  return str.replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
};

document.addEventListener('mouseup', () => {
    const selection = window.getSelection();
    if (selection.toString().trim().length > 0) {
        const range = selection.getRangeAt(0);
        const container = range.commonAncestorContainer;
        
        // Find the transcript-row
        let row = container;
        while (row && (!row.classList || !row.classList.contains('transcript-row'))) {
            row = row.parentNode;
        }

        if (row) {
            const rowContent = row.querySelector('.row-content');
            const rowId = row.getAttribute('data-row-id');
            
            // Calculate offsets relative to rowContent text
            const preSelectionRange = range.cloneRange();
            preSelectionRange.selectNodeContents(rowContent);
            preSelectionRange.setEnd(range.startContainer, range.startOffset);
            const start = preSelectionRange.toString().length;
            const end = start + selection.toString().length;

            currentSelectionData = {
                rowId: rowId,
                start: start,
                end: end,
                text: selection.toString(),
                card: row.closest('.cuj-card')
            };
        }
    }
});

function openNoteModal() {
    if (!currentSelectionData) {
        alert("Please highlight some text within a transcript turn first.");
        return;
    }

    const note = {
      id: 'note-' + Date.now(),
      rowId: currentSelectionData.rowId,
      start: currentSelectionData.start,
      end: currentSelectionData.end,
      originalText: currentSelectionData.text,
      noteText: 'New note...'
    };

    notesData.push(note);
    renderNotesForCard(currentSelectionData.card);

    // Find the new note in the sidebar and focus it
    const card = currentSelectionData.card;
    const sidebar = card.querySelector('.notes-sidebar');
    const noteItems = sidebar.querySelectorAll('.note-text');
    const newNoteEl = noteItems[noteItems.length - 1];
    if (newNoteEl) {
      newNoteEl.focus();
      const range = document.createRange();
      range.selectNodeContents(newNoteEl);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }

    currentSelectionData = null;
}

let deleteTimeout = null;
let itemToDelete = null;

function showToast(message, undoCallback, deleteCallback) {
  const toast = document.getElementById('toast-container');
  const messageEl = document.getElementById('toast-message');
  const undoBtn = document.getElementById('toast-undo-btn');

  if (!toast) return;

  messageEl.innerText = message;
  toast.classList.remove('hidden');

  if (deleteTimeout) {
    clearTimeout(deleteTimeout);
    if (itemToDelete && itemToDelete.deleteFn) {
      itemToDelete.deleteFn();
    }
  }

  itemToDelete = {deleteFn: deleteCallback};

  undoBtn.onclick = () => {
    clearTimeout(deleteTimeout);
    toast.classList.add('hidden');
    if (undoCallback) undoCallback();
    itemToDelete = null;
  };

  deleteTimeout = setTimeout(() => {
    toast.classList.add('hidden');
    if (deleteCallback) deleteCallback();
    itemToDelete = null;
  }, 10000);
}

function deleteNote(noteId, card) {
  const originalNotesData = [...notesData];
  notesData = notesData.filter(n => n.id !== noteId);
  renderNotesForCard(card);

  showToast('Note deleted.', () => {
    notesData = originalNotesData;
    renderNotesForCard(card);
  }, () => {});
}

function renderNotesForCard(card) {
    const sidebar = card.querySelector('.notes-sidebar');
    sidebar.innerHTML = '<h4>Notes</h4>';
    
    // Find all notes for rows in this card
    const cardRowIds = Array.from(card.querySelectorAll('.transcript-row')).map(r => r.getAttribute('data-row-id'));
    const cardNotes = notesData.filter(n => cardRowIds.includes(n.rowId));

    cardNotes.forEach(note => {
        const noteItem = document.createElement('div');
        noteItem.className = 'note-item';
        noteItem.innerHTML = `
            <div class="note-header">
                <span class="note-ref">Ref: "${
            escapeHtml(note.originalText)}"</span>
                <div class="note-actions">
                    <button onclick="deleteNote('${
            note.id}', this.closest('.cuj-card'))">Delete</button>
                </div>
            </div>
            <div class="note-text" contenteditable="true" onblur="updateNoteText('${
            note.id}', this.innerText)">${escapeHtml(note.noteText)}</div>
        `;
        sidebar.appendChild(noteItem);
    });

    // Re-apply highlights to rows
    cardRowIds.forEach(rowId => {
        const row = card.querySelector(`[data-row-id="${rowId}"]`);
        const rowContent = row.querySelector('.row-content');
        const rowNotes = cardNotes.filter(n => n.rowId === rowId);
        
        applyHighlights(rowContent, rowNotes);
    });
}

function updateNoteText(noteId, newText) {
    const note = notesData.find(n => n.id === noteId);
    if (note) note.noteText = newText;
}

function applyHighlights(container, notes) {
    const text = container.innerText;
    const coverage = Array.from({length: text.length}, () => []);
    notes.forEach(note => {
      for (let i = note.start; i < note.end; i++) {
        if (i >= 0 && i < text.length) {
          coverage[i].push(note);
        }
      }
    });

    const notesSetChanged = (setA, setB) => {
      if (setA.length !== setB.length) return true;
      for (let i = 0; i < setA.length; i++) {
        if (setA[i].id !== setB[i].id) return true;
      }
      return false;
    };

    let finalHtml = '';
    let currentNotes = [];
    let segmentStart = 0;

    for (let i = 0; i <= text.length; i++) {
      const nextNotes = i < text.length ? coverage[i] : [];
      if (notesSetChanged(currentNotes, nextNotes) || i === text.length) {
        if (i > segmentStart) {
          const segmentText = text.substring(segmentStart, i);
          if (currentNotes.length > 0) {
            const combinedTitle =
                currentNotes.map(n => escapeHtml(n.noteText)).join(' | ');
            const className =
                currentNotes.length > 1 ? 'highlight overlap' : 'highlight';
            finalHtml += `<span class="${className}" title="${combinedTitle}">${
                escapeHtml(segmentText)}</span>`;
          } else {
            finalHtml += escapeHtml(segmentText);
          }
        }
        currentNotes = nextNotes;
        segmentStart = i;
      }
    }


    if (finalHtml.includes('[Tool:'))
      finalHtml = finalHtml.replace(
          /\[Tool:(?:<[^>]+>|.)*?\]/g,
          match => `<span class="tool">${match}</span>`);
    if (finalHtml.includes('[Webhook:'))
      finalHtml = finalHtml.replace(
          /\[Webhook:(?:<[^>]+>|.)*?\]/g,
          match => `<span class="webhook">${match}</span>`);

    container.innerHTML = finalHtml;
}

function moveRowUp(btn) {
    const row = btn.closest('.transcript-row');
    const prev = row.previousElementSibling;
    if (prev && prev.classList.contains('transcript-row')) {
        row.parentNode.insertBefore(row, prev);
    }
}

function moveRowDown(btn) {
    const row = btn.closest('.transcript-row');
    const next = row.nextElementSibling;
    if (next && next.classList.contains('transcript-row')) {
        row.parentNode.insertBefore(next, row);
    }
}

function deleteRow(btn) {
  const row = btn.closest('.transcript-row');
  row.style.display = 'none';

  showToast(
      'Row deleted.',
      () => {
        row.style.display = '';
      },
      () => {
        row.remove();
      });
}

function addRow(type, btn) {
    const container = btn.closest('.transcript-container');
    const newRow = document.createElement('div');
    newRow.className = 'transcript-row';
    newRow.setAttribute('data-row-id', 'row-' + Date.now());

    let speakerHtml = '';
    let contentHtml = '';
    let isCall = (type === 'T' || type === 'W');

    if (type === 'A') {
      speakerHtml = '<span class="agent">Agent:</span>';
      contentHtml = '"New turn..."';
    } else if (type === 'U') {
      speakerHtml = '<span class="user">User:</span>';
      contentHtml = '"New turn..."';
    } else if (type === 'T' || type === 'W') {
      const callType = type === 'T' ? 'tool' : 'webhook';
      const callLabel = type === 'T' ? 'Tool' : 'Webhook';
      let urlInfo = '';
      if (type === 'W') {
        urlInfo = 'POST https://api.example.com/v1/action\n';
      }

      contentHtml = CALL_ITEM_TEMPLATE
          .replace('$CALL_TYPE', `call-item-${callType}`)
          .replace('$CALL_LABEL', callLabel)
          .replace('$NAME', `New_${callType}`)
          .replace('$URL_INFO', urlInfo)
          .replace('$PAYLOAD', '{\n  "param": "value"\n}')
          .replace('$RESPONSE', '{\n  "status": "success"\n}');
    }

    newRow.innerHTML = `
        <div class="row-toolbar">
            <button onclick="openNoteModal()">+Note</button>
            <button onclick="addRow('A', this)">+A</button>
            <button onclick="addRow('U', this)">+U</button>
            <button onclick="addRow('T', this)">+T</button>
            <button onclick="addRow('W', this)">+W</button>
            <button onclick="moveRowUp(this)">↑</button>
            <button onclick="moveRowDown(this)">↓</button>
            <button onclick="deleteRow(this)">x</button>
        </div>
        ${speakerHtml}
        <div class="row-content" ${isCall ? '' : 'contenteditable="true"'}>
            ${contentHtml}
        </div>
    `;

    const currentRow = btn.closest('.transcript-row');
    if (currentRow) {
        currentRow.after(newRow);
    } else {
        container.appendChild(newRow);
    }

    // Add click listener for accordion
    const header = newRow.querySelector('.call-item-header');
    if (header) {
      header.addEventListener('click', () => {
        const callItem = header.closest('.call-item');
        if (callItem) {
          callItem.classList.toggle('collapsed');
        }
      });
    }
}

function downloadReport() {
    // Embed notesData into the script so they persist
    const parser = new DOMParser();
    const doc = parser.parseFromString(document.documentElement.outerHTML, 'text/html');
    
    // Remove existing data script if any
    const existingScript = doc.getElementById('notes-data');
    if (existingScript) existingScript.remove();
    
    const dataScript = doc.createElement('script');
    dataScript.id = 'notes-data';
    dataScript.type = 'application/json';
    dataScript.textContent = JSON.stringify(notesData);
    doc.body.appendChild(dataScript);
    
    let currentHtml = doc.documentElement.outerHTML;

    const blob = new Blob([currentHtml], {type: 'text/html'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'subintent_report.html';
    a.click();
}

function resetHighlights() {
    if (confirm("Clear all notes and highlights?")) {
        notesData = [];
        document.querySelectorAll('.cuj-card').forEach(renderNotesForCard);
    }
}

// Initialization
function init() {
    // Handle rows that were hydrated from reconstruction script
    document
        .querySelectorAll(
            '.transcript-container p, .transcript-container .turn, .transcript-container .call-item')
        .forEach((el, idx) => {
          // Skip if disconnected or already inside a transcript-row
          if (!el.isConnected) return;
          if (el.closest('.transcript-row')) return;

          const row = document.createElement('div');
          row.className = 'transcript-row';
          row.setAttribute(
              'data-row-id',
              el.getAttribute('data-row-id') || 'row-init-' + idx);

          const speakerSpan = el.querySelector('.agent, .user');
          const speakerHtml = speakerSpan ? speakerSpan.outerHTML : '';

          let contentHtml = '';
          let isCall = el.classList.contains('call-item');

          if (isCall) {
            const clone = el.cloneNode(true);
            contentHtml = clone.outerHTML;
          } else {
            const contentWrapper =
                el.querySelector('.row-content-wrapper') || el;
            if (contentWrapper === el) {
              const clone = el.cloneNode(true);
              const sp = clone.querySelector('.agent, .user');
              if (sp) sp.remove();
              contentHtml = clone.innerHTML;
            } else {
              contentHtml = contentWrapper.innerHTML;
            }
          }

          row.innerHTML = `
            <div class="row-toolbar">
                <button onclick="openNoteModal()">+Note</button>
                <button onclick="addRow('A', this)">+A</button>
                <button onclick="addRow('U', this)">+U</button>
                <button onclick="addRow('T', this)">+T</button>
                <button onclick="addRow('W', this)">+W</button>
                <button onclick="moveRowUp(this)">↑</button>
                <button onclick="moveRowDown(this)">↓</button>
                <button onclick="deleteRow(this)">x</button>
            </div>
            ${speakerHtml}
            <div class="row-content" ${isCall ? '' : 'contenteditable="true"'}>
                ${contentHtml}
            </div>
        `;
          el.replaceWith(row);
          
          // Add click listener for accordion
          const header = row.querySelector('.call-item-header');
          if (header) {
            header.addEventListener('click', () => {
              const callItem = header.closest('.call-item');
              if (callItem) {
                callItem.classList.toggle('collapsed');
              }
            });
          }
        });

    // Restructure cards for Flex Layout
    document.querySelectorAll('.cuj-card').forEach(card => {
        if (!card.querySelector('.main-content')) {
            const wrapper = document.createElement('div');
            wrapper.className = 'main-content';
            while (card.firstChild) wrapper.appendChild(card.firstChild);
            
            const sidebar = document.createElement('div');
            sidebar.className = 'notes-sidebar';
            sidebar.innerHTML = '<h4>Notes</h4>';
            
            card.appendChild(wrapper);
            card.appendChild(sidebar);
        }
    });

    // Load persisted notes if any
    const dataScript = document.getElementById('notes-data');
    if (dataScript) {
        notesData = JSON.parse(dataScript.textContent);
        document.querySelectorAll('.cuj-card').forEach(renderNotesForCard);
    } else if (window.loadedNotes) {
        notesData = window.loadedNotes;
        document.querySelectorAll('.cuj-card').forEach(renderNotesForCard);
    }

    // Initialize theme toggle button state
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    const iconSpan = document.getElementById('themeToggleIcon');
    const textSpan = document.getElementById('themeToggleText');
    if (iconSpan && textSpan) {
        if (currentTheme === 'dark') {
            iconSpan.textContent = '☀️';
            textSpan.textContent = 'Light Mode';
        } else {
            iconSpan.textContent = '🌙';
            textSpan.textContent = 'Dark Mode';
        }
    }
}

window.addEventListener('load', init);
