import argparse
import json
import os
import random
import string
import sys

CHECKLIST_FILE = "task_checklist.json"


def generate_key(length=16):
  return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def load_checklist():
  if os.path.exists(CHECKLIST_FILE):
    with open(CHECKLIST_FILE, "r") as f:
      return json.load(f)
  return {"title": "Default Checklist", "key": "", "items": {}}


def save_checklist(checklist):
  with open(CHECKLIST_FILE, "w") as f:
    json.dump(checklist, f, indent=2)


def init(args):
  key = generate_key()
  checklist = {"title": args.title, "key": key, "items": {}}
  save_checklist(checklist)
  print(f"Initialized checklist: {args.title}")
  print(f"Unique Key: {key}")


def add(args):
  checklist = load_checklist()
  checklist["items"][args.item] = {
      "status": "pending",
      "output_check_path": args.output_check_path or "",
      "comments": [],
  }
  save_checklist(checklist)
  print(f"Added item: {args.item}")
  print(f"Total items: {len(checklist['items'])}")


def done(args):
  checklist = load_checklist()
  if args.item not in checklist["items"]:
    print(f"Error: Item '{args.item}' not found in checklist.")
    sys.exit(1)

  item = checklist["items"][args.item]

  # Verification step
  if item["output_check_path"]:
    if not os.path.exists(item["output_check_path"]):
      print(
          f"Error: Output not found at '{item['output_check_path']}'. Cannot"
          " mark as done."
      )
      sys.exit(1)
    else:
      if os.path.isdir(item["output_check_path"]):
        if not os.listdir(item["output_check_path"]):
          print(
              f"Error: Output directory '{item['output_check_path']}' is empty."
              " Cannot mark as done."
          )
          sys.exit(1)
      elif os.path.isfile(item["output_check_path"]):
        if os.path.getsize(item["output_check_path"]) == 0:
          print(
              f"Error: Output file '{item['output_check_path']}' is empty."
              " Cannot mark as done."
          )
          sys.exit(1)

  item["status"] = "done"
  save_checklist(checklist)
  print(f"Marked as done: {args.item}")


def comment(args):
  checklist = load_checklist()
  if args.item not in checklist["items"]:
    print(f"Error: Item '{args.item}' not found in checklist.")
    sys.exit(1)

  checklist["items"][args.item]["comments"].append(args.text)
  save_checklist(checklist)
  print(f"Added comment to '{args.item}': {args.text}")


def status(args):
  checklist = load_checklist()
  print(f"Checklist: {checklist['title']}")
  print(f"Key: {checklist.get('key', 'N/A')}")
  print(json.dumps(checklist["items"], indent=2))

  done_count = sum(
      1 for item in checklist["items"].values() if item["status"] == "done"
  )
  total_count = len(checklist["items"])
  print(f"Progress: {done_count}/{total_count} completed.")


def main():
  parser = argparse.ArgumentParser(
      description="Manage a deterministic task checklist."
  )
  subparsers = parser.add_subparsers(dest="command", required=True)

  # Init command
  parser_init = subparsers.add_parser(
      "init", help="Initialize a new checklist."
  )
  parser_init.add_argument(
      "--title", required=True, help="Title of the checklist."
  )
  parser_init.set_defaults(func=init)

  # Add command
  parser_add = subparsers.add_parser(
      "add", help="Add an item to the checklist."
  )
  parser_add.add_argument(
      "--item", required=True, help="Name of the checklist item."
  )
  parser_add.add_argument(
      "--output_check_path",
      help="Path to check for output before marking done.",
  )
  parser_add.set_defaults(func=add)

  # Done command
  parser_done = subparsers.add_parser("done", help="Mark an item as done.")
  parser_done.add_argument(
      "--item", required=True, help="Name of the checklist item."
  )
  parser_done.set_defaults(func=done)

  # Comment command
  parser_comment = subparsers.add_parser(
      "comment", help="Add a comment to an item."
  )
  parser_comment.add_argument(
      "--item", required=True, help="Name of the checklist item."
  )
  parser_comment.add_argument("--text", required=True, help="Comment text.")
  parser_comment.set_defaults(func=comment)

  # Status command
  parser_status = subparsers.add_parser("status", help="Show checklist status.")
  parser_status.set_defaults(func=status)

  args = parser.parse_args()
  args.func(args)


if __name__ == "__main__":
  main()
