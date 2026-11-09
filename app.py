import os
import json
import sqlite3
from functools import wraps

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

from flask import (
    Flask,
    g,
    render_template,
    request,
    send_from_directory,
    redirect,
    url_for,
    session,
    jsonify,
)
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import uuid
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATABASE_PATH = os.path.join(BASE_DIR, "scorpio.db")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.secret_key = os.environ.get("SCORPIO_SECRET_KEY", "scorpio-dev-secret")

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
CHART_FOLDER = os.path.join(STATIC_DIR, "charts")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CHART_FOLDER, exist_ok=True)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_: object | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            share_token TEXT NOT NULL UNIQUE,
            context_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    db.commit()


with app.app_context():
    init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def get_current_user() -> dict | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    db = get_db()
    row = db.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        session.pop("user_id", None)
        return None
    return {"id": int(row["id"]), "username": row["username"]}


def get_asset_version(filename: str) -> int:
    path = os.path.join(STATIC_DIR, filename)
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return 1


@app.context_processor
def inject_auth_user() -> dict:
    return {
        "auth_user": get_current_user(),
        "style_v": get_asset_version("style.css"),
        "main_v": get_asset_version("main.js"),
    }


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    df = df.drop_duplicates().copy()
    numeric_cols = df.select_dtypes(include="number").columns
    for col in numeric_cols:
        df[col] = df[col].fillna(df[col].mean())
    for col in df.select_dtypes(exclude="number").columns:
        mode = df[col].mode(dropna=True)
        fill_value = mode.iloc[0] if not mode.empty else "Unknown"
        df[col] = df[col].fillna(fill_value)
    return df


def generate_chart(df: pd.DataFrame) -> str | None:
    numeric_df = df.select_dtypes(include="number")
    if numeric_df.empty:
        return None
    chart_name = f"chart_{uuid.uuid4().hex[:8]}.png"
    chart_path = os.path.join(CHART_FOLDER, chart_name)
    numeric_df.hist(figsize=(10, 6))
    plt.tight_layout()
    plt.savefig(chart_path)
    plt.close()
    return f"/static/charts/{chart_name}"


def generate_correlation_heatmap(df: pd.DataFrame) -> str | None:
    numeric_df = df.select_dtypes(include="number")
    if numeric_df.shape[1] < 2:
        return None
    corr = numeric_df.corr(numeric_only=True)
    chart_name = f"corr_{uuid.uuid4().hex[:8]}.png"
    chart_path = os.path.join(CHART_FOLDER, chart_name)
    plt.figure(figsize=(8, 6))
    plt.imshow(corr, cmap="coolwarm", aspect="auto")
    plt.colorbar(label="Correlation")
    plt.xticks(range(len(corr.columns)), corr.columns, rotation=45, ha="right")
    plt.yticks(range(len(corr.columns)), corr.columns)
    plt.title("Correlation Heatmap")
    plt.tight_layout()
    plt.savefig(chart_path)
    plt.close()
    return f"/static/charts/{chart_name}"


def generate_outlier_table(df: pd.DataFrame) -> str:
    numeric_df = df.select_dtypes(include="number")
    if numeric_df.empty:
        return "<p>No numeric columns available for outlier detection.</p>"

    records = []
    for col in numeric_df.columns:
        q1 = numeric_df[col].quantile(0.25)
        q3 = numeric_df[col].quantile(0.75)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        outlier_count = int(((numeric_df[col] < lower) | (numeric_df[col] > upper)).sum())
        records.append(
            {
                "Column": col,
                "Q1": round(float(q1), 4),
                "Q3": round(float(q3), 4),
                "IQR": round(float(iqr), 4),
                "Lower Bound": round(float(lower), 4),
                "Upper Bound": round(float(upper), 4),
                "Outlier Count": outlier_count,
            }
        )
    outlier_df = pd.DataFrame(records)
    return outlier_df.to_html(classes="summary-table", border=0, index=False)


def generate_missing_table(before_df: pd.DataFrame, after_df: pd.DataFrame) -> str:
    missing_before = before_df.isna().sum()
    missing_after = after_df.isna().sum()
    summary = pd.DataFrame(
        {
            "Column": before_df.columns,
            "Missing Before": [int(missing_before[col]) for col in before_df.columns],
            "Missing After": [int(missing_after[col]) for col in before_df.columns],
        }
    )
    return summary.to_html(classes="summary-table", border=0, index=False)


def build_kpi_cards(before_df: pd.DataFrame, after_df: pd.DataFrame) -> dict:
    duplicates_removed = int(before_df.duplicated().sum())
    total_missing_before = int(before_df.isna().sum().sum())
    total_missing_after = int(after_df.isna().sum().sum())
    return {
        "rows": int(after_df.shape[0]),
        "columns": int(after_df.shape[1]),
        "numeric_cols": int(after_df.select_dtypes(include="number").shape[1]),
        "categorical_cols": int(after_df.select_dtypes(exclude="number").shape[1]),
        "duplicates_removed": duplicates_removed,
        "missing_before": total_missing_before,
        "missing_after": total_missing_after,
    }


def generate_predictive_insights(df: pd.DataFrame) -> dict:
    numeric_df = df.select_dtypes(include="number")
    if numeric_df.empty:
        return {
            "available": False,
            "message": "No numeric columns available for predictive modeling.",
        }

    candidate_col = None
    candidate_series = None
    best_var = -1.0

    for col in numeric_df.columns:
        series = pd.to_numeric(numeric_df[col], errors="coerce").dropna().reset_index(drop=True)
        if len(series) < 3:
            continue
        variance = float(series.var())
        if variance > best_var:
            best_var = variance
            candidate_col = col
            candidate_series = series

    if candidate_col is None or candidate_series is None:
        return {
            "available": False,
            "message": "Not enough numeric observations to build a trend forecast.",
        }

    y = candidate_series.astype(float)
    n = len(y)
    x = pd.Series(range(n), dtype="float64")

    sum_x = float(x.sum())
    sum_y = float(y.sum())
    sum_xy = float((x * y).sum())
    sum_x2 = float((x * x).sum())
    denom = n * sum_x2 - (sum_x**2)

    if denom == 0:
        slope = 0.0
        intercept = float(y.mean())
    else:
        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

    fitted = intercept + slope * x
    ss_res = float(((y - fitted) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 if ss_tot == 0 else max(0.0, 1 - (ss_res / ss_tot))

    horizon = min(12, max(5, n // 5))
    future_x = pd.Series(range(n, n + horizon), dtype="float64")
    future_y = intercept + slope * future_x

    forecast_df = pd.DataFrame(
        {
            "Future Index": [int(i) for i in future_x],
            f"Predicted {candidate_col}": [round(float(v), 4) for v in future_y],
        }
    )

    chart_name = f"predict_{uuid.uuid4().hex[:8]}.png"
    chart_path = os.path.join(CHART_FOLDER, chart_name)

    plt.figure(figsize=(10, 5.4))
    plt.plot(x, y, label="Observed", linewidth=2.2, color="#2f7dff")
    plt.plot(x, fitted, label="Trend Line", linestyle="--", linewidth=2, color="#18c9b9")
    plt.plot(future_x, future_y, label="Forecast", linewidth=2.2, color="#ffb454")
    plt.axvline(x=n - 1, linestyle=":", color="#c0d6ff", linewidth=1)
    plt.title(f"Predictive Trend for {candidate_col}")
    plt.xlabel("Observation Index")
    plt.ylabel(candidate_col)
    plt.legend()
    plt.tight_layout()
    plt.savefig(chart_path)
    plt.close()

    trend = "Upward" if slope > 0 else "Downward" if slope < 0 else "Flat"
    change_pct = 0.0
    if n > 0 and float(y.iloc[-1]) != 0:
        change_pct = ((float(future_y.iloc[-1]) - float(y.iloc[-1])) / abs(float(y.iloc[-1]))) * 100

    return {
        "available": True,
        "chart": f"/static/charts/{chart_name}",
        "target_column": candidate_col,
        "equation": f"y = {slope:.4f}x + {intercept:.4f}",
        "r2": round(r2, 4),
        "trend": trend,
        "projected_change_pct": round(change_pct, 2),
        "forecast_table": forecast_df.to_html(classes="summary-table", border=0, index=False),
    }


def _max_outlier_ratio(df: pd.DataFrame) -> float:
    numeric_df = df.select_dtypes(include="number")
    if numeric_df.empty:
        return 0.0
    max_ratio = 0.0
    for col in numeric_df.columns:
        series = numeric_df[col].dropna()
        if series.empty:
            continue
        q1 = series.quantile(0.25)
        q3 = series.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        ratio = float(((series < lower) | (series > upper)).mean())
        max_ratio = max(max_ratio, ratio)
    return max_ratio


def generate_prescriptive_insights(before_df: pd.DataFrame, after_df: pd.DataFrame) -> dict:
    rows = max(1, int(after_df.shape[0]))
    missing_before = int(before_df.isna().sum().sum())
    missing_ratio = missing_before / max(1, int(before_df.shape[0] * before_df.shape[1]))

    outlier_ratio = _max_outlier_ratio(after_df)

    numeric_df = after_df.select_dtypes(include="number")
    if numeric_df.shape[1] >= 2:
        corr = numeric_df.corr(numeric_only=True).abs()
        for col in corr.columns:
            corr.loc[col, col] = 0
        max_corr = float(corr.max().max()) if not corr.empty else 0.0
    else:
        max_corr = 0.0

    categorical_cols = int(after_df.select_dtypes(exclude="number").shape[1])

    actions = [
        {
            "action": "Harden missing-value ingestion rules",
            "score": min(100, round(missing_ratio * 280 + 10, 1)),
            "reason": f"Pre-clean missing values were {missing_before}. Add upstream null checks and mandatory-field validation.",
            "impact": "Higher data reliability and fewer imputations.",
        },
        {
            "action": "Audit outlier-heavy numeric fields",
            "score": min(100, round(outlier_ratio * 240 + 12, 1)),
            "reason": f"Maximum outlier ratio detected is about {outlier_ratio * 100:.1f}% in numeric features.",
            "impact": "Improves model stability and reduces noisy predictions.",
        },
        {
            "action": "Control multicollinearity in features",
            "score": min(100, round(max_corr * 100, 1)),
            "reason": f"Peak absolute correlation is {max_corr:.2f}. Consider dropping or combining highly correlated features.",
            "impact": "More interpretable models and better generalization.",
        },
        {
            "action": "Encode and standardize categorical inputs",
            "score": min(100, round(categorical_cols * 11 + 8, 1)),
            "reason": f"Detected {categorical_cols} categorical columns. Build a consistent encoding layer.",
            "impact": "Unlocks stronger predictive features from text/category columns.",
        },
        {
            "action": "Expand sample size for robust training",
            "score": 78.0 if rows < 200 else 44.0 if rows < 1000 else 20.0,
            "reason": f"Current row count is {rows}. Larger datasets reduce variance and overfitting risk.",
            "impact": "Better forecast confidence and reduced drift sensitivity.",
        },
    ]

    actions = sorted(actions, key=lambda item: item["score"], reverse=True)
    top_actions = actions[:3]

    chart_name = f"prescribe_{uuid.uuid4().hex[:8]}.png"
    chart_path = os.path.join(CHART_FOLDER, chart_name)

    chart_actions = actions[:5]
    labels = [a["action"] for a in chart_actions]
    scores = [a["score"] for a in chart_actions]

    plt.figure(figsize=(10, 5.6))
    bars = plt.barh(labels[::-1], scores[::-1], color=["#ffb454", "#18c9b9", "#2f7dff", "#8fd3ff", "#9ccf8e"])
    for bar in bars:
        width = bar.get_width()
        plt.text(width + 1.5, bar.get_y() + bar.get_height() / 2, f"{width:.1f}", va="center", fontsize=9)
    plt.xlim(0, 110)
    plt.xlabel("Priority Score")
    plt.title("Prescriptive Action Priorities")
    plt.tight_layout()
    plt.savefig(chart_path)
    plt.close()

    return {
        "chart": f"/static/charts/{chart_name}",
        "recommendations": top_actions,
    }


def save_cleaned_csv(df: pd.DataFrame, original_filename: str) -> str:
    base_name = os.path.splitext(original_filename)[0]
    clean_name = f"{base_name}_cleaned_{uuid.uuid4().hex[:6]}.csv"
    save_path = os.path.join(UPLOAD_FOLDER, clean_name)
    df.to_csv(save_path, index=False)
    return clean_name


def build_report_context(before_df: pd.DataFrame, cleaned_df: pd.DataFrame, filename: str) -> dict:
    summary = cleaned_df.describe(include="all").transpose().fillna("").to_html(classes="summary-table", border=0)
    chart_path = generate_chart(cleaned_df)
    correlation_chart = generate_correlation_heatmap(cleaned_df)
    outlier_table = generate_outlier_table(cleaned_df)
    missing_table = generate_missing_table(before_df, cleaned_df)
    kpis = build_kpi_cards(before_df, cleaned_df)
    preview_table = cleaned_df.head(10).to_html(classes="summary-table", border=0, index=False)
    cleaned_filename = save_cleaned_csv(cleaned_df, filename)
    predictive_insight = generate_predictive_insights(cleaned_df)
    prescriptive_insight = generate_prescriptive_insights(before_df, cleaned_df)

    return {
        "tables": summary,
        "chart": chart_path,
        "correlation_chart": correlation_chart,
        "outlier_table": outlier_table,
        "missing_table": missing_table,
        "kpis": kpis,
        "preview_table": preview_table,
        "cleaned_filename": cleaned_filename,
        "predictive_insight": predictive_insight,
        "prescriptive_insight": prescriptive_insight,
    }


def read_uploaded_dataframe(filepath: str, filename: str) -> pd.DataFrame:
    lower = filename.lower()
    if lower.endswith(".csv"):
        return pd.read_csv(filepath)
    if lower.endswith(".xlsx"):
        return pd.read_excel(filepath)
    raise ValueError("Unsupported file format")


def save_report(user_id: int, report_name: str, context: dict) -> tuple[int, str]:
    share_token = uuid.uuid4().hex[:16]
    context_json = json.dumps(context)
    db = get_db()
    cursor = db.execute(
        "INSERT INTO reports (user_id, name, share_token, context_json) VALUES (?, ?, ?, ?)",
        (user_id, report_name, share_token, context_json),
    )
    db.commit()
    return int(cursor.lastrowid), share_token


def get_latest_report_for_user(user_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT id, name, share_token, context_json, created_at FROM reports WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "share_token": row["share_token"],
        "created_at": row["created_at"],
        "context": json.loads(row["context_json"]),
    }


def get_report_for_user(report_id: int, user_id: int) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT id, name, share_token, context_json, created_at FROM reports WHERE id = ? AND user_id = ?",
        (report_id, user_id),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "share_token": row["share_token"],
        "created_at": row["created_at"],
        "context": json.loads(row["context_json"]),
    }


def list_reports_for_user(user_id: int) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, name, share_token, created_at FROM reports WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "share_token": row["share_token"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_report_by_share_token(share_token: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT id, name, share_token, context_json, created_at FROM reports WHERE share_token = ?",
        (share_token,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "share_token": row["share_token"],
        "created_at": row["created_at"],
        "context": json.loads(row["context_json"]),
    }


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3:
            error = "Username must be at least 3 characters."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            db = get_db()
            exists = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if exists:
                error = "Username already exists."
            else:
                db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
                db.commit()
                return redirect(url_for("login"))

    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        row = db.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            error = "Invalid username or password."
        else:
            session["user_id"] = int(row["id"])
            next_path = request.args.get("next")
            return redirect(next_path or url_for("dashboard"))

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = get_current_user()
    report = get_latest_report_for_user(user["id"])
    if report is None:
        return render_template("dashboard.html", has_data=False)

    context = report["context"]
    context["share_url"] = f"/share/{report['share_token']}"
    context["report_name"] = report["name"]
    context["report_created_at"] = report["created_at"]
    return render_template("dashboard.html", has_data=True, **context)


@app.route("/sources")
@login_required
def sources():
    return render_template("sources.html")


@app.route("/reports")
@login_required
def reports():
    user = get_current_user()
    report_list = list_reports_for_user(user["id"])
    return render_template("reports.html", reports=report_list)


@app.route("/reports/<int:report_id>")
@login_required
def view_report(report_id: int):
    user = get_current_user()
    report = get_report_for_user(report_id, user["id"])
    if report is None:
        return redirect(url_for("reports"))
    context = report["context"]
    context["share_url"] = f"/share/{report['share_token']}"
    context["report_name"] = report["name"]
    context["report_created_at"] = report["created_at"]
    context["shared_view"] = False
    return render_template("result.html", **context)


@app.route("/share/<string:share_token>")
def shared_report(share_token: str):
    report = get_report_by_share_token(share_token)
    if report is None:
        return redirect(url_for("home"))
    context = report["context"]
    context["share_url"] = f"/share/{report['share_token']}"
    context["report_name"] = report["name"]
    context["report_created_at"] = report["created_at"]
    context["shared_view"] = True
    return render_template("result.html", **context)


@app.route("/api/reports/latest")
@login_required
def api_latest_report():
    user = get_current_user()
    report = get_latest_report_for_user(user["id"])
    if report is None:
        return jsonify({"ok": False, "error": "No reports found"}), 404

    context = report["context"]
    response = {
        "ok": True,
        "report": {
            "id": report["id"],
            "name": report["name"],
            "created_at": report["created_at"],
            "share_url": f"/share/{report['share_token']}",
            "kpis": context.get("kpis", {}),
            "predictive": context.get("predictive_insight", {}),
            "prescriptive": context.get("prescriptive_insight", {}),
        },
    }
    return jsonify(response)


@app.route("/download/<path:filename>")
@login_required
def download_file(filename: str):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    user = get_current_user()

    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            return render_template("upload.html", error="Please choose a CSV or Excel file.")

        filename = secure_filename(file.filename)
        if not filename.lower().endswith((".csv", ".xlsx")):
            return render_template("upload.html", error="Supported formats: .csv, .xlsx")

        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        try:
            df = read_uploaded_dataframe(filepath, filename)
        except Exception as exc:
            return render_template("upload.html", error=f"Could not read file: {exc}")

        cleaned_df = clean_data(df)
        context = build_report_context(df, cleaned_df, filename)

        report_name = f"{os.path.splitext(filename)[0]} Report"
        report_id, share_token = save_report(user["id"], report_name, context)
        context["share_url"] = f"/share/{share_token}"
        context["report_id"] = report_id
        context["report_name"] = report_name

        context["shared_view"] = False
        return render_template("result.html", **context)

    return render_template("upload.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5002"))
    app.run(debug=True, port=port)
