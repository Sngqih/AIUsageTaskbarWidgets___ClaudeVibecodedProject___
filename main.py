"""Start UsageTray. Run with pythonw.exe so no console window appears.

    main.py --smoke-test   check the install/build works, then exit (0 = OK)
"""

import sys

if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        from usagetray.smoke import run
        sys.exit(run())
    from usagetray.app import main
    sys.exit(main())
