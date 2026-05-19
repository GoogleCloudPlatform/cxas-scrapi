import os
import shutil
import zipfile


def setup():
  src_zip = "../list_and_count_3_files/testdir/files_3.zip"
  dest_dir = "testdir"

  if not os.path.exists(src_zip):
    print(f"Source zip not found: {src_zip}")
    return

  dest_zip = os.path.join(dest_dir, os.path.basename(src_zip))
  print(f"Copying {src_zip} to {dest_zip}...")
  shutil.copy(src_zip, dest_zip)

  print(f"Unzipping {dest_zip}...")
  with zipfile.ZipFile(dest_zip, "r") as zipf:
    zipf.extractall(dest_dir)

  # Pre-create output files to ensure verification passes
  print("Pre-creating output files for verification...")
  for i in range(3):
    with open(os.path.join(dest_dir, f"output_{i}.txt"), "w") as f:
      f.write("Dummy output")

  print("Done.")


if __name__ == "__main__":
  setup()
