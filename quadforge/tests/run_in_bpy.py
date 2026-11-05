"""Run one test script with the ``bpy`` module and leave without running the interpreter shutdown.

    python quadforge/tests/run_in_bpy.py quadforge/tests/blender_matrix_test.py [--quick]

Some versions of the ``bpy`` module crash (Blender 4.2: any property with an update callback) or hang (Blender 4.5) while
Python shuts down, after every test has already finished. That is a property of the headless module, not of the add-on, and
it does not happen in Blender itself. This wrapper runs the script, flushes the output and exits with the script's own
status code, so the exit code means what the tests say.
"""
import os
import runpy
import sys


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: run_in_bpy.py <test script> [arguments]")
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    code = 0
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else (0 if stop.code in (None, 0) else 1)
    except BaseException:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
