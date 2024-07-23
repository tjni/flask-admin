from flask import Flask
from flask_babel import Babel
from flask_sqlalchemy import SQLAlchemy

import flask_admin as admin
from geoalchemy2.types import Geometry

from flask_admin.theme import Bootstrap4Theme
from flask_admin.contrib.geoa import ModelView


# Create application
app = Flask(__name__)
babel = Babel(app)
app.config.from_pyfile('config.py')
db = SQLAlchemy(app)


class Point(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    point = db.Column(Geometry("POINT"))


class MultiPoint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    point = db.Column(Geometry("MULTIPOINT"))


class Polygon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    point = db.Column(Geometry("POLYGON"))


class MultiPolygon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    point = db.Column(Geometry("MULTIPOLYGON"))


class LineString(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    point = db.Column(Geometry("LINESTRING"))


class MultiLineString(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    point = db.Column(Geometry("MULTILINESTRING"))


# Flask views
@app.route('/')
def index():
    return '<a href="/admin/">Click me to get to Admin!</a>'

# Create admin
admin = admin.Admin(app, name='Example: GeoAlchemy', theme=Bootstrap4Theme())


class ModalModelView(ModelView):
    edit_modal = True

# Add views
admin.add_view(ModalModelView(Point, db.session, category='Points'))
admin.add_view(ModalModelView(MultiPoint, db.session, category='Points'))
admin.add_view(ModalModelView(Polygon, db.session, category='Polygons'))
admin.add_view(ModalModelView(MultiPolygon, db.session, category='Polygons'))
admin.add_view(ModalModelView(LineString, db.session, category='Lines'))
admin.add_view(ModalModelView(MultiLineString, db.session, category='Lines'))

if __name__ == '__main__':

    with app.app_context():
        db.create_all()

    # Start app
    app.run(debug=True)
