from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default="user")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Record(db.Model):
    __tablename__ = "records"
    id = db.Column(db.Integer, primary_key=True)
    owner = db.Column(db.String(64), nullable=False)
    agent = db.Column(db.String(64), nullable=False)
    category = db.Column(db.String(64), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    price = db.Column(db.Float, default=0)
    note = db.Column(db.String(200), default="")
    # 确保 month 字段存在且有默认值
    month = db.Column(db.String(7), nullable=True, default=lambda: datetime.now().strftime("%Y-%m"))

    @property
    def total(self):
        return (self.quantity or 0) * (self.price or 0)

class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(256), nullable=False)

    @staticmethod
    def get(key, default=None):
        item = Setting.query.get(key)
        return item.value if item else default

    @staticmethod
    def set(key, value):
        item = Setting.query.get(key)
        if item:
            item.value = value
        else:
            db.session.add(Setting(key=key, value=value))
        db.session.commit()

class Agent(db.Model):
    __tablename__ = "agents"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)

class PriceRule(db.Model):
    __tablename__ = "price_rules"
    id = db.Column(db.Integer, primary_key=True)
    agent = db.Column(db.String(64), nullable=False)
    category = db.Column(db.String(64), nullable=False)
    price = db.Column(db.Float, default=0)
    __table_args__ = (db.UniqueConstraint("agent", "category", name="uq_agent_category"),)
