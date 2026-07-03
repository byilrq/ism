from datetime import date
from flask_login import UserMixin
from app import db, login_manager


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)


class Asset(db.Model):
    __tablename__ = "assets"

    id = db.Column(db.Integer, primary_key=True)
    group_no = db.Column(db.String(64), unique=True, nullable=True)
    internal_no = db.Column(db.String(64), unique=True, nullable=True)
    name = db.Column(db.String(255), nullable=False)
    model = db.Column(db.String(255))
    owner = db.Column(db.String(100))
    location = db.Column(db.String(100))
    asset_date = db.Column(db.Date, default=date.today)
    status = db.Column(db.String(50))
    remark = db.Column(db.Text)
    image_path = db.Column(db.String(500))

    images = db.relationship(
        "AssetImage",
        backref="asset",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="AssetImage.created_at.asc()"
    )


class Accessory(db.Model):
    __tablename__ = "accessories"

    id = db.Column(db.Integer, primary_key=True)
    parent_asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), nullable=True)
    sub_group_no = db.Column(db.String(64), unique=True, nullable=True)
    sub_internal_no = db.Column(db.String(64), unique=True, nullable=True)
    name = db.Column(db.String(255), nullable=False)
    model = db.Column(db.String(255))
    owner = db.Column(db.String(100))
    location = db.Column(db.String(100))
    asset_date = db.Column(db.Date, default=date.today)
    status = db.Column(db.String(50))
    remark = db.Column(db.Text)
    image_path = db.Column(db.String(500))

    images = db.relationship(
        "AccessoryImage",
        backref="accessory",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="AccessoryImage.created_at.asc()"
    )


class AssetImage(db.Model):
    __tablename__ = "asset_images"

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    image_path = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class AccessoryImage(db.Model):
    __tablename__ = "accessory_images"

    id = db.Column(db.Integer, primary_key=True)
    accessory_id = db.Column(db.Integer, db.ForeignKey("accessories.id", ondelete="CASCADE"), nullable=False)
    image_path = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class DictOption(db.Model):
    __tablename__ = "dict_options"

    id = db.Column(db.Integer, primary_key=True)
    dict_type = db.Column(db.String(50), nullable=False)
    dict_value = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
