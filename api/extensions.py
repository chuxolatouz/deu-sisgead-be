from flask_pymongo import PyMongo
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from flasgger import Swagger
from flask_mail import Mail

mongo = PyMongo()
bcrypt = Bcrypt()
cors = CORS()
swagger = Swagger()
mail = Mail()
