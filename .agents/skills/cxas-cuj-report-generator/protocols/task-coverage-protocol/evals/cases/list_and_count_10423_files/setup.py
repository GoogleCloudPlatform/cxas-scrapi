import os
import zipfile


def setup():
  inputs_dir = "testdir"
  zip_files = [f for f in os.listdir(inputs_dir) if f.endswith(".zip")]
  if not zip_files:
    print("No zip file found in testdir/")
    return

  zip_path = os.path.join(inputs_dir, zip_files[0])
  print(f"Unzipping {zip_path}...")
  with zipfile.ZipFile(zip_path, "r") as zipf:
    zipf.extractall(inputs_dir)
  print("Done.")


if __name__ == "__main__":
  setup()
