try:  # local dev convenience; not installed in the production image
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=app.config["APP_ENV"] == "dev")
