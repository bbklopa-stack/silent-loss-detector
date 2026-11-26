from datetime import datetime
from functools import wraps
from io import StringIO
from pathlib import Path
import sqlite3
import csv
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
import pandas as pd
from engine import run_audit

app = Flask(__name__)
app.secret_key = "silent-loss-detector-free-access"

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "silent_loss.db"

DEFAULT_FREE_PLAN = {
    "name": "وصول مجاني كامل",
    "price": "مجاني",
    "max_uploads": 9999,
}


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                company_name TEXT NOT NULL,
                sector TEXT NOT NULL,
                sector_title TEXT NOT NULL,
                total_expense_leak REAL NOT NULL,
                slump_days TEXT NOT NULL,
                losing_products_count INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                cost REAL NOT NULL,
                extra REAL NOT NULL,
                real_cost REAL NOT NULL,
                price REAL NOT NULL,
                net_profit REAL NOT NULL,
                status TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES audit_runs (id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


def save_audit_to_db(company_name, sector_key, report):
    slump_text = " — ".join(report.get("slump_days", []))
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO audit_runs (
                created_at, company_name, sector, sector_title,
                total_expense_leak, slump_days, losing_products_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at, company_name, sector_key,
                report.get("sector_title", sector_key),
                float(report.get("total_expense_leak", 0)),
                slump_text, int(report.get("losing_products_count", 0)),
            ),
        )
        run_id = cursor.lastrowid

        for prod in report.get("products", []):
            conn.execute(
                """
                INSERT INTO audit_products (
                    run_id, name, cost, extra, real_cost, price, net_profit, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, str(prod.get("name", "")), float(prod.get("cost", 0)),
                    float(prod.get("extra", 0)), float(prod.get("real_cost", 0)),
                    float(prod.get("price", 0)), float(prod.get("net_profit", 0)),
                    str(prod.get("status", "")),
                ),
            )
        conn.commit()


def get_recent_runs(limit=20):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, company_name, sector_title,
                   total_expense_leak, slump_days, losing_products_count
            FROM audit_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def clear_all_runs():
    with get_db_connection() as conn:
        conn.execute("DELETE FROM audit_products")
        conn.execute("DELETE FROM audit_runs")
        conn.commit()


def build_recommendations(report):
    recs = []
    abnormal = [e for e in report.get("expenses", []) if e.get("is_abnormal")]
    if abnormal:
        top_exp = max(abnormal, key=lambda e: float(e.get("diff", 0)))
        recs.append({
            "type": "danger",
            "title": "مراجعة بند المصروفات الأعلى انحرافاً",
            "detail": f"البند «{top_exp.get('item')}» يتجاوز المعيار بفارق +${top_exp.get('diff')} (إجمالي الانحراف: ${report.get('total_expense_leak', 0)}).",
        })
    else:
        recs.append({
            "type": "ok",
            "title": "المصروفات ضمن الحدود الطبيعية",
            "detail": "لا توجد انحرافات شاذة مرتفعة في بنود المصروفات الحالية.",
        })

    losing = [p for p in report.get("products", []) if float(p.get("net_profit", 0)) < 0]
    if losing:
        worst_prod = min(losing, key=lambda p: float(p.get("net_profit", 0)))
        recs.append({
            "type": "danger",
            "title": "إعادة تسعير أو خفض تكلفة المنتجات الخاسرة",
            "detail": f"يوجد {len(losing)} منتجاً بهامش سالب؛ الأكثر خسارة هو «{worst_prod.get('name')}» بصافي ${worst_prod.get('net_profit')}.",
        })
    else:
        recs.append({
            "type": "ok",
            "title": "هوامش ربح إيجابية لجميع المنتجات",
            "detail": "جميع المنتجات المحللة تحقق صافي ربح صفري أو موجباً.",
        })

    slump_days = report.get("slump_days", [])
    if slump_days:
        recs.append({
            "type": "info",
            "title": "تعزيز المبيعات في فترات التراجع",
            "detail": f"لوحظ تراجع في المبيعات خلال: {' — '.join(str(d) for d in slump_days)}؛ يُنصح بعروض أو حملات مستهدفة فيها.",
        })
    return recs


def compute_health_score(report):
    score = 100
    leak = max(0.0, float(report.get("total_expense_leak", 0)))
    losing_count = max(0, int(report.get("losing_products_count", 0)))
    slump_count = len(report.get("slump_days", []))

    score -= min(40, int(leak // 25))
    score -= min(35, losing_count * 10)
    score -= min(25, slump_count * 8)
    score = max(0, min(100, score))

    if score >= 75:
        label, state = "مستقر", "ok"
    elif score >= 45:
        label, state = "يحتاج متابعة", "info"
    else:
        label, state = "تسرب مرتفع", "danger"

    return {"score": score, "label": label, "state": state}


def build_comparison(report, recent_runs, is_post):
    if not recent_runs:
        return None
    prev = recent_runs[1] if (is_post and len(recent_runs) > 1) else (recent_runs[0] if not is_post else None)
    if not prev:
        return None

    cur_leak = round(float(report.get("total_expense_leak", 0)), 2)
    prev_leak = round(float(prev.get("total_expense_leak", 0)), 2)
    cur_losing = int(report.get("losing_products_count", 0))
    prev_losing = int(prev.get("losing_products_count", 0))

    return {
        "prev_time": prev["created_at"],
        "prev_sector": prev["sector_title"],
        "leak_diff": round(cur_leak - prev_leak, 2),
        "losing_diff": cur_losing - prev_losing,
    }


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "company_email" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped_view


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        mode = request.form.get("mode", "account")
        if mode == "guest":
            session.clear()
            clear_all_runs()
            session["company_email"] = "guest@temp.local"
            session["company_name"] = "ضيف مؤقت"
            session["is_guest"] = True
            session["plan"] = "free"
            session["uploads_used"] = 0
            session["preferred_sector"] = "clothing"
            flash("أنت الآن في وضع الضيف؛ سيتم حذف بيانات هذه الجلسة لاحقاً عند إعادة البدء.", "info")
            return redirect(url_for("index"))

        email = request.form.get("email", "").strip().lower() or "user@archive.local"
        company_name = request.form.get("company_name", "").strip() or "حساب محفوظ"
        default_sector = request.form.get("default_sector", "clothing")

        session["company_email"] = email
        session["company_name"] = company_name
        session["is_guest"] = False
        session["plan"] = "free"
        session["uploads_used"] = 0
        session["preferred_sector"] = default_sector
        flash(f"مرحباً {company_name} — أرشيفك وحساباتك محفوظة.", "success")
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    is_guest = session.get("is_guest", False)
    session.clear()
    if is_guest:
        clear_all_runs()
        flash("تم إنهاء جلسة الضيف وحذف بياناتها المؤقتة؛ يمكنك البدء من جديد.", "info")
    else:
        flash("تم تسجيل الخروج من الجلسة الحالية، وسجلات أرشيفك محفوظة في قاعدة البيانات.", "info")
    return redirect(url_for("login"))


@app.route("/plans", methods=["GET", "POST"])
@login_required
def plans():
    session["plan"] = "free"
    if request.method == "POST":
        flash("تم تفعيل الوصول المجاني الكامل بدون أي رسوم.", "success")
    return render_template(
        "plans.html",
        plan={
            "name": DEFAULT_FREE_PLAN["name"],
            "price": DEFAULT_FREE_PLAN["price"],
            "features": [
                "وصول كامل لجميع القطاعات الأربعة",
                "تحليل المصروفات الشاذة وتراجع المبيعات وهوامش الربح",
                "حفظ سجل التدقيق وتصديره ومسحه بسهولة",
            ],
        },
        company_name=session.get("company_name", "حساب مجاني"),
    )


@app.route("/add-product", methods=["POST"])
@login_required
def add_product():
    init_db()
    name = request.form.get("prod_name", "").strip()
    sector = request.form.get("sector", session.get("preferred_sector", "clothing"))
    session["preferred_sector"] = sector

    try:
        cost = float(request.form.get("prod_cost", 0))
        extra = float(request.form.get("prod_extra", 0))
        price = float(request.form.get("prod_price", 0))
    except ValueError:
        flash("يرجى إدخال أرقام صحيحة للتكلفة والسعر.", "error")
        return redirect(url_for("index"))

    if not name:
        flash("يرجى كتابة اسم المنتج أولاً.", "error")
        return redirect(url_for("index"))

    items = list(session.get("custom_products", []))
    items.append({"name": name, "cost": cost, "extra": extra, "price": price})
    session["custom_products"] = items

    company_name = session.get("company_name", "حساب مجاني")
    updated_report = run_audit(sector, items)
    save_audit_to_db(company_name, sector, updated_report)
    session["just_updated"] = True

    flash(f"تمت إضافة «{name}» وتحديث المصروفات وتحليل الكلفة والنتائج فوراً.", "success")
    return redirect(url_for("index"))


@app.route("/clear-custom-products", methods=["POST"])
@login_required
def clear_custom_products():
    init_db()
    session.pop("custom_products", None)
    sector = session.get("preferred_sector", "clothing")
    company_name = session.get("company_name", "حساب مجاني")
    updated_report = run_audit(sector, None)
    save_audit_to_db(company_name, sector, updated_report)
    session["just_updated"] = True
    flash("تمت إزالة المنتجات اليدوية وتحديث التدقيق.", "info")
    return redirect(url_for("index"))


@app.route("/export-history")
@login_required
def export_history():
    init_db()
    rows = get_recent_runs(limit=500)

    output = StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow([
        "id", "created_at", "company_name", "sector_title",
        "total_expense_leak", "slump_days", "losing_products_count",
    ])
    for r in rows:
        writer.writerow([
            r["id"], r["created_at"], r["company_name"], r["sector_title"],
            r["total_expense_leak"], r["slump_days"], r["losing_products_count"],
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_history.csv"},
    )


@app.route("/export-products", methods=["POST"])
@login_required
def export_products():
    fallback_sector = session.get("preferred_sector", "clothing")
    selected_sector = request.form.get("sector", fallback_sector)
    custom_products = session.get("custom_products") or None

    uploaded_file = request.files.get("file")
    if uploaded_file and uploaded_file.filename:
        df = pd.read_csv(uploaded_file)
        required_cols = {"name", "cost", "extra", "price"}
        if required_cols.issubset(df.columns):
            custom_products = df[list(required_cols)].to_dict(orient="records")

    report = run_audit(selected_sector, custom_products)

    output = StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["name", "cost", "extra", "real_cost", "price", "net_profit", "status"])
    for prod in report.get("products", []):
        writer.writerow([
            prod.get("name", ""), prod.get("cost", 0), prod.get("extra", 0),
            prod.get("real_cost", 0), prod.get("price", 0), prod.get("net_profit", 0),
            prod.get("status", ""),
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=products_audit.csv"},
    )


@app.route("/clear-history", methods=["POST"])
@login_required
def clear_history():
    init_db()
    clear_all_runs()
    flash("تم مسح سجل عمليات التدقيق من قاعدة البيانات.", "success")
    return redirect(url_for("index"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    init_db()
    company_name = session.get("company_name", "حساب مجاني")
    fallback_sector = session.get("preferred_sector", "clothing")
    selected_sector = request.form.get("sector", fallback_sector)
    if request.method == "POST":
        session["preferred_sector"] = selected_sector

    custom_products = session.get("custom_products") or None

    uploaded_file = request.files.get("file")
    if uploaded_file and uploaded_file.filename:
        df = pd.read_csv(uploaded_file)
        required_cols = {"name", "cost", "extra", "price"}
        if required_cols.issubset(df.columns):
            custom_products = df[list(required_cols)].to_dict(orient="records")
            session.pop("custom_products", None)
            session["uploads_used"] = session.get("uploads_used", 0) + 1
        else:
            flash("يجب أن يحتوي ملف CSV على الأعمدة: name, cost, extra, price", "error")

    report = run_audit(selected_sector, custom_products)
    just_updated = session.pop("just_updated", False)
    is_post = (request.method == "POST") or just_updated

    if request.method == "POST":
        save_audit_to_db(company_name, selected_sector, report)
        flash("تم حفظ نتائج التدقيق في قاعدة بيانات SQLite.", "success")

    recent_runs = get_recent_runs()

    return render_template(
        "index.html",
        report=report,
        selected_sector=selected_sector,
        company_name=company_name,
        plan_name=DEFAULT_FREE_PLAN["name"],
        uploads_used=session.get("uploads_used", 0),
        custom_count=len(session.get("custom_products", [])),
        recent_runs=recent_runs,
        recommendations=build_recommendations(report),
        health=compute_health_score(report),
        comparison=build_comparison(report, recent_runs, is_post),
    )


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)