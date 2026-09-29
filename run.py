"""Run SpeakrFlow from source. Use this for development.
For an installable .app, run: python setup.py py2app
"""
if __name__ == "__main__":
    from src.diagnostics import configure_logging
    configure_logging()
    from src.app import main
    main()
