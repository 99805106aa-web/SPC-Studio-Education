"""Cloud entrypoint: fail closed in education mode without changing desktop defaults."""
import os
from pathlib import Path
import runpy

os.environ["SPC_EDUCATION_MODE"] = "true"
runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
