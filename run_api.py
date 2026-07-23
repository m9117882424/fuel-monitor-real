import uvicorn

from app.api import app
from app.month_comments import install_month_comments


install_month_comments(app)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
