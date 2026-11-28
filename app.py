import os
import csv
import io
import re
from functools import wraps
from datetime import datetime
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, Response, jsonify)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from sqlalchemy import func, text
from models import db, User, Record, Setting, Agent, PriceRule

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
db_path = os.environ.get("DATABASE_PATH", "/data/app.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            flash("需要管理员权限", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated

USERNAME_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9_-]{2,32}$")
CATEGORIES = ["100-200", "250-300", "350-", "400+", "监管", "安卓500下", "安卓500上"]
DEFAULT_PRICES = {"100-200": 40, "250-300": 80, "350-": 100,
                  "400+": 120, "监管": 100, "安卓500下": 28, "安卓500上": 68}
DEFAULT_AGENTS = ["星空", "小泪", "小C", "怡丰", "小林", "小蕾", "狂妄", "小乐"]

def init_db():
    with app.app_context():
        db.create_all()
        # 强行补全可能缺失的月份字段
        try:
            db.session.execute(text("SELECT month FROM records LIMIT 1"))
        except Exception:
            db.session.rollback()
            try:
                current_m = datetime.now().strftime("%Y-%m")
                db.session.execute(text(f"ALTER TABLE records ADD COLUMN month VARCHAR(7) DEFAULT '{current_m}'"))
                db.session.commit()
            except Exception:
                db.session.rollback()

        if not User.query.filter_by(username="000").first():
            admin = User(username="000", role="admin")
            admin.set_password("000")
            db.session.add(admin)
        if Setting.get("registration_open") is None:
            Setting.set("registration_open", "0")
            
        current_m = datetime.now().strftime("%Y-%m")
        if Setting.get("active_month") is None:
            Setting.set("active_month", current_m)

        for name in DEFAULT_AGENTS:
            if not Agent.query.filter_by(name=name).first():
                db.session.add(Agent(name=name))
        for agent in DEFAULT_AGENTS:
            for cat, p in DEFAULT_PRICES.items():
                if not PriceRule.query.filter_by(agent=agent, category=cat).first():
                    db.session.add(PriceRule(agent=agent, category=cat, price=p))
        db.session.commit()

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("账号或密码错误", "error")
    return render_template("login.html",
                           registration_open=(Setting.get("registration_open", "0") == "1"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if Setting.get("registration_open", "0") != "1":
        flash("当前未开放注册，请联系管理员", "error")
        return redirect(url_for("login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not USERNAME_RE.match(username):
            flash("用户名格式错误", "error")
            return render_template("register.html")
        if password == "" or password != confirm:
            flash("密码不能为空或两次不一致", "error")
            return render_template("register.html")
        if User.query.filter(func.lower(User.username) == username.lower()).first():
            flash("该用户名已被注册", "error")
            return render_template("register.html")
        u = User(username=username, role="user")
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        flash("注册成功", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    active_month = Setting.get("active_month", datetime.now().strftime("%Y-%m"))
    selected_month = request.args.get("month", active_month)
    
    query = Record.query
    # 查询逻辑优化：如果选了具体月份，找不到对应月份的旧数据也拉出来
    if selected_month != "all":
        query = query.filter((Record.month == selected_month) | (Record.month == None) | (Record.month == ""))
        
    if current_user.role == "admin":
        selected_user = request.args.get("user", "all")
        if selected_user != "all":
            query = query.filter_by(owner=selected_user)
        users = User.query.all()
    else:
        selected_user = current_user.username
        query = query.filter_by(owner=current_user.username)
        users = [current_user]
        
    records = query.all()
    
    all_months_res = db.session.query(Record.month).distinct().all()
    month_options = [m[0] for m in all_months_res if m[0]]
    if active_month not in month_options:
        month_options.append(active_month)
    month_options.sort(reverse=True)

    all_agents = [a.name for a in Agent.query.order_by(Agent.id).all()]
    categories = CATEGORIES

    summary = {a: {c: {'qty': 0, 'amt': 0} for c in categories} for a in all_agents}
    agent_totals = {a: {'qty': 0, 'amt': 0} for a in all_agents}
    category_totals = {c: {'qty': 0, 'amt': 0} for c in categories}
    grand_total = {'qty': 0, 'amt': 0}
    user_summary = {}
    
    if current_user.role == "admin" and selected_user == "all":
        for u in users:
            user_summary[u.username] = {'qty': 0, 'amt': 0}

    for r in records:
        qty = r.quantity or 0
        amt = qty * (r.price or 0)
        
        agent_name = r.agent if r.agent in summary else "已删除中介"
        if agent_name not in summary:
            summary[agent_name] = {c: {'qty': 0, 'amt': 0} for c in categories}
            agent_totals[agent_name] = {'qty': 0, 'amt': 0}

        if r.category in summary[agent_name]:
            summary[agent_name][r.category]['qty'] += qty
            summary[agent_name][r.category]['amt'] += amt
            
        agent_totals[agent_name]['qty'] += qty
        agent_totals[agent_name]['amt'] += amt
        
        if r.category in category_totals:
            category_totals[r.category]['qty'] += qty
            category_totals[r.category]['amt'] += amt
            
        grand_total['qty'] += qty
        grand_total['amt'] += amt

        if current_user.role == "admin" and selected_user == "all":
            if r.owner in user_summary:
                user_summary[r.owner]['qty'] += qty
                user_summary[r.owner]['amt'] += amt
        elif current_user.role != "admin":
            if current_user.username not in user_summary:
                user_summary[current_user.username] = {'qty': 0, 'amt': 0}
            user_summary[current_user.username]['qty'] += qty
            user_summary[current_user.username]['amt'] += amt

    return render_template("dashboard.html", 
                           records=records,
                           is_admin=(current_user.role == "admin"),
                           users=users,
                           selected_user=selected_user,
                           selected_month=selected_month,
                           active_month=active_month,
                           month_options=month_options,
                           all_agents=all_agents,
                           categories=categories,
                           summary=summary,
                           agent_totals=agent_totals,
                           category_totals=category_totals,
                           grand_total=grand_total,
                           user_summary=user_summary,
                           registration_open=(Setting.get("registration_open", "0") == "1"))

@app.route("/admin/new-month", methods=["POST"])
@login_required
@admin_required
def create_new_month():
    new_month = request.form.get("new_month", "").strip()
    if not re.match(r"^\d{4}-\d{2}$", new_month):
        flash("月份格式错误，必须为 YYYY-MM", "error")
    else:
        Setting.set("active_month", new_month)
        flash(f"成功创建并切换到新表：{new_month}", "success")
    return redirect(url_for("dashboard", month=new_month))

@app.route("/record/add", methods=["POST"])
@login_required
def add_record():
    if current_user.role == "admin":
        owner = request.form.get("owner")
        if not owner or not User.query.filter_by(username=owner).first():
            flash("请选择有效的所属账号", "error")
            return redirect(url_for("dashboard"))
        price = float(request.form.get("price") or 0)
    else:
        owner = current_user.username
        price = None
        
    agent = request.form.get("agent", "").strip()
    category = request.form.get("category", "").strip()
    quantity = int(request.form.get("quantity") or 0)
    note = request.form.get("note", "").strip()
    
    active_month = Setting.get("active_month", datetime.now().strftime("%Y-%m"))

    if not agent or not category:
        flash("中介和类别不能为空", "error")
        return redirect(url_for("dashboard"))
        
    if current_user.role != "admin":
        if not Agent.query.filter_by(name=agent).first():
            flash("该中介不存在，请联系管理员添加", "error")
            return redirect(url_for("dashboard"))
        rule = PriceRule.query.filter_by(agent=agent, category=category).first()
        price = rule.price if rule else 0
        
    rec = Record(owner=owner, agent=agent, category=category,
                 quantity=quantity, price=price or 0, note=note, month=active_month)
    db.session.add(rec)
    db.session.commit()
    flash(f"数据已自动保存至 {active_month} 统计表", "success")
    return redirect(url_for("dashboard"))

@app.route("/record/<int:rid>/edit", methods=["GET", "POST"])
@login_required
def edit_record(rid):
    rec = Record.query.get_or_404(rid)
    if current_user.role != "admin" and rec.owner != current_user.username:
        flash("无权编辑此记录", "error")
        return redirect(url_for("dashboard"))
        
    if request.method == "POST":
        if current_user.role == "admin":
            new_owner = request.form.get("owner", rec.owner)
            if User.query.filter_by(username=new_owner).first():
                rec.owner = new_owner
            rec.agent = request.form.get("agent", rec.agent)
            rec.price = float(request.form.get("price") or 0)
            
        rec.category = request.form.get("category", rec.category)
        rec.quantity = int(request.form.get("quantity") or 0)
        rec.note = request.form.get("note", "").strip()
        db.session.commit()
        flash("更新成功，数据已自动保存", "success")
        return redirect(url_for("dashboard", month=rec.month or "all"))
        
    users = User.query.order_by(User.id).all() if current_user.role == "admin" else [current_user]
    all_agents = Agent.query.order_by(Agent.name).all()
    return render_template("edit.html", record=rec, users=users,
                           is_admin=(current_user.role == "admin"),
                           all_agents=all_agents)

@app.route("/record/<int:rid>/delete", methods=["POST"])
@login_required
def delete_record(rid):
    rec = Record.query.get_or_404(rid)
    if current_user.role != "admin" and rec.owner != current_user.username:
        flash("无权删除", "error")
        return redirect(url_for("dashboard"))
    month = rec.month or "all"
    db.session.delete(rec)
    db.session.commit()
    flash("记录已删除", "success")
    return redirect(url_for("dashboard", month=month))

@app.route("/record/<int:rid>/price", methods=["POST"])
@login_required
@admin_required
def update_price(rid):
    rec = Record.query.get_or_404(rid)
    rec.price = float(request.form.get("price") or 0)
    db.session.commit()
    flash("价格已更新", "success")
    return redirect(url_for("dashboard", month=rec.month or "all"))

@app.route("/agents", methods=["GET", "POST"])
@login_required
@admin_required
def manage_agents():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name and not Agent.query.filter_by(name=name).first():
            db.session.add(Agent(name=name))
            db.session.commit()
            flash("中介已添加", "success")
        else:
            flash("中介名为空或已存在", "error")
        return redirect(url_for("manage_agents"))
    agents = Agent.query.order_by(Agent.name).all()
    return render_template("agents.html", agents=agents)

# 这里是终极解决方案：放弃前端的 fetch，直接用 GET 请求在服务端执行清理，然后重定向回页面
@app.route("/agents/force_delete/<int:aid>")
@login_required
@admin_required
def force_delete_agent(aid):
    a = Agent.query.get_or_404(aid)
    agent_name = a.name
    try:
        # 1. 暴力清理价目表
        PriceRule.query.filter_by(agent=agent_name).delete()
        
        # 2. 暴力清理历史记录，把旧记录强行改名，彻底解除数据库的外键或逻辑绑定
        Record.query.filter_by(agent=agent_name).update({"agent": f"已删除_{agent_name}"})
        
        # 3. 删中介
        db.session.delete(a)
        db.session.commit()
        flash(f"中介【{agent_name}】及其关联数据已彻底移除！", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"数据库拒绝删除：{str(e)}", "error")
    
    return redirect(url_for("manage_agents"))

@app.route("/prices", methods=["GET", "POST"])
@login_required
@admin_required
def manage_prices():
    agents = Agent.query.order_by(Agent.name).all()
    if request.method == "POST":
        agent = request.form.get("agent")
        category = request.form.get("category")
        price = float(request.form.get("price") or 0)
        rule = PriceRule.query.filter_by(agent=agent, category=category).first()
        if rule:
            rule.price = price
        else:
            db.session.add(PriceRule(agent=agent, category=category, price=price))
        db.session.commit()
        flash("价目已更新", "success")
        return redirect(url_for("manage_prices"))
    rules = {(r.agent, r.category): r.price for r in PriceRule.query.all()}
    return render_template("prices.html", agents=agents,
                           categories=CATEGORIES, rules=rules)

@app.route("/admin/users")
@login_required
@admin_required
def manage_users():
    users = User.query.order_by(User.id).all()
    counts = {u.username: Record.query.filter_by(owner=u.username).count() for u in users}
    return render_template("users.html", users=users, counts=counts,
                           current_username=current_user.username)

@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
@login_required
@admin_required
def delete_user(uid):
    user = User.query.get_or_404(uid)
    if user.username == current_user.username:
        flash("不能删除当前登录的自己", "error")
        return redirect(url_for("manage_users"))
    if user.role == "admin" and User.query.filter_by(role="admin").count() <= 1:
        flash("至少保留一个管理员账号", "error")
        return redirect(url_for("manage_users"))
    Record.query.filter_by(owner=user.username).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f"已删除用户 {user.username} 及其记录", "success")
    return redirect(url_for("manage_users"))

@app.route("/admin/users/<int:uid>/password", methods=["GET", "POST"])
@login_required
@admin_required
def change_user_password(uid):
    user = User.query.get_or_404(uid)
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm", "")
        if new_password == "":
            flash("密码不能为空", "error")
        elif new_password != confirm:
            flash("两次密码不一致", "error")
        else:
            user.set_password(new_password)
            db.session.commit()
            flash(f"已修改 {user.username} 的密码", "success")
            return redirect(url_for("manage_users"))
    return render_template("change_password.html", user=user)

@app.route("/admin/toggle-registration", methods=["POST"])
@login_required
@admin_required
def toggle_registration():
    current = Setting.get("registration_open", "0")
    Setting.set("registration_open", "0" if current == "1" else "1")
    flash("注册功能已" + ("开启" if current == "0" else "关闭"), "success")
    return redirect(url_for("dashboard"))

@app.route("/export")
@login_required
@admin_required
def export_csv():
    records = Record.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "月份", "所属账号", "中介", "类别", "数量", "价格", "合计", "备注"])
    for r in records:
        writer.writerow([r.id, r.month or "未知", r.owner, r.agent, r.category,
                         r.quantity, r.price, r.total, r.note])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=monthly_data.csv"})

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
