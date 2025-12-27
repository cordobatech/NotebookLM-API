import uvicorn
import os
import argparse
from dotenv import load_dotenv

def main():
    parser = argparse.ArgumentParser(description="Start the NotebookLM Automator API server.")
    parser.add_argument("--port", type=int, default=8000, help="API server port")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="API server host")
    parser.add_argument("--notebook-url", type=str, help="NotebookLM Notebook URL (overrides NOTEBOOKLM_URL env var)")
    parser.add_argument("--headless", action="store_true", help="Run Chrome in headless mode (no GUI)")
    parser.add_argument("--cookies-file", type=str, help="Path to cookies.txt file for auto-login (Netscape format)")

    args = parser.parse_args()

    # Load .env file if exists
    load_dotenv()

    if args.notebook_url:
        os.environ["NOTEBOOKLM_URL"] = args.notebook_url

    if args.headless:
        os.environ["NOTEBOOKLM_HEADLESS"] = "1"

    if args.cookies_file:
        os.environ["NOTEBOOKLM_COOKIES_FILE"] = args.cookies_file

    if not os.getenv("NOTEBOOKLM_URL"):
        print("Error: NOTEBOOKLM_URL environment variable or argument is required.")
        return

    headless_mode = "headless" if args.headless else "GUI"
    print(f"Starting API server for notebook: {os.getenv('NOTEBOOKLM_URL')} ({headless_mode} mode)")

    uvicorn.run("notebooklm_automator.api.app:app", host=args.host, port=args.port, reload=True)

if __name__ == "__main__":
    main()
