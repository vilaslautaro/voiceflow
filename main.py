import os
import sys

# When launched via pythonw.exe (no console), stdout/stderr are None.
# Libraries like torch/whisper crash when trying to print.
# Redirect to devnull to prevent this.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from gui.app import App


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
