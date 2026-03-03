from flask import Flask
from app.routes import register_routes


def create_app():
    application = Flask(__name__)
    register_routes(application)
    return application
