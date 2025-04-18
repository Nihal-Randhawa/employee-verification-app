"""
Employee Data Verification Portal (Streamlit)
===========================================
• One submission per employee   • Works great on phones
• Easy OTP login                • Simple language & clear steps
"""

# ────────────────────────────────────────────────
# Imports
# ────────────────────────────────────────────────
import hashlib, random, string, time, datetime
from pathlib import Path
import pandas as pd, streamlit as st
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib, gspread
from google.oauth2.service_account import Credentials

# ────────────────────────────────────────────────
# Settings
# ────────────────────────────────────────────────
ALLOWED_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com"}
OTP_VALID_FOR_SEC, RESEND_COOLDOWN_SEC, MAX_OTP_ATTEMPTS = 300, 30, 3
EXCEL_FILE = "Employee Master IT 2.0.xlsx"
LOG_SHEET_NAME, LOCAL_CSV = "Verified Corrections Log", "verified_corrections_log.csv"

# Free‑text name fields
NAME_COLS = {
    "employee_first_name",
    "employee_middle_name",
    "employee_last_name",
    "employee_father_name",
}
# Always treat as date
FORCE_DATE_COLS = {"date_of_substantive_entry"}

EMAIL_ADDRESS = st.secrets["EMAIL_ADDRESS"]
EMAIL_PASSWORD = st.secrets["EMAIL_PASSWORD"]

# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────
@st.cache_data
def load_master() -> pd.DataFrame:
    df = pd.read_excel(EXCEL_FILE)
    return df.set_index("employee_id")

df_master = load_master()

# Dropdown choices for tidy columns
DROP_OPTIONS: dict[str, list[str]] = {}
for col in df_master.columns:
    if col in NAME_COLS or col in FORCE_DATE_COLS:
        continue
    if pd.api.types.is_datetime64_any_dtype(df_master[col]):
        continue
    vals = (
        df_master[col]
        .fillna("")
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )
    DROP_OPTIONS[col] = sorted(v for v in vals if v)

# Tiny helpers
make_otp = lambda n=6: "".join(random.choices(string.digits, k=n))
sha = lambda s: hashlib.sha256(s.encode()).hexdigest()


def send_otp(email: str, otp: str):
    msg = MIMEMultipart()
    msg["From"], msg["To"] = EMAIL_ADDRESS, email
    msg["Subject"] = "Your one‑time password"
    msg.attach(MIMEText(f"Your 6‑digit code is {otp}. It works for the next 5 minutes.", "plain"))
    with smtplib.SMTP("smtp.gmail.com", 587) as srv:
        srv.starttls(); srv.login(EMAIL_ADDRESS, EMAIL_PASSWORD); srv.sendmail(EMAIL_ADDRESS, email, msg.as_string())


def get_sheet(name: str):
    creds = Credentials.from_service_account_info(st.secrets["gspread_service_account"])
    return gspread.authorize(creds).open(name).sheet1


def already_done(emp_id: int) -> bool:
    try:
        return str(emp_id) in get_sheet(LOG_SHEET_NAME).col_values(1)
    except Exception:
        p = Path(LOCAL_CSV)
        return p.exists() and str(emp_id) in pd.read_csv(p, usecols=["employee_id"], dtype=str)["employee_id"].tolist()


def save_row(row: dict):
    try:
        get_sheet(LOG_SHEET_NAME).append_row([str(row[k]) for k in row])
    except Exception as e:
        p = Path(LOCAL_CSV)
        pd.DataFrame([row]).to_csv(p, mode="a", header=not p.exists(), index=False)
        st.warning(f"Saved offline (Google Sheet was not reachable).")

# ────────────────────────────────────────────────
# Session defaults
# ────────────────────────────────────────────────
for k, v in {
    "otp_hash": "",
    "otp_time": 0.0,
    "otp_attempts": 0,
    "otp_sent": False,
    "authenticated": False,
    "email": "",
    "employee_id": "",
}.items():
    st.session_state.setdefault(k, v)

# ════════════════════════════════════════════════
# 1. Login with email + code
# ════════════════════════════════════════════════
if not st.session_state.authenticated:
    st.title("🔐 Check your details")
    with st.form("login"):
        email = st.text_input("Personal email (Gmail / Yahoo / Outlook)", value=st.session_state.email)
        emp_id = st.text_input("Employee ID", value=st.session_state.employee_id)
        if st.form_submit_button("Send code"):
            if not any(email.lower().endswith("@" + d) for d in ALLOWED_EMAIL_DOMAINS):
                st.error("Please use a Gmail, Yahoo or Outlook address.")
            elif not emp_id.isdigit() or int(emp_id) not in df_master.index:
                st.error("That Employee ID isn’t in our list.")
            elif already_done(int(emp_id)):
                st.error("You’ve already finished this step. Contact HR if you need to change something.")
            elif time.time() - st.session_state.otp_time < RESEND_COOLDOWN_SEC:
                st.warning("Please wait a few seconds and tap again.")
            else:
                otp = make_otp(); send_otp(email, otp)
                st.session_state.update({
                    "otp_hash": sha(otp),
                    "otp_time": time.time(),
                    "otp_attempts": 0,
                    "otp_sent": True,
                    "email": email.strip(),
                    "employee_id": emp_id.strip(),
                })
                st.success("Code sent! Check your mail.")

    if st.session_state.otp_sent:
        with st.form("code"):
            code = st.text_input("6‑digit code", max_chars=6)
            if st.form_submit_button("Log in"):
                if time.time() - st.session_state.otp_time > OTP_VALID_FOR_SEC:
                    st.error("Code expired. Tap “Send code” again."); st.session_state.otp_sent=False
                elif st.session_state.otp_attempts>=MAX_OTP_ATTEMPTS:
                    st.error("Too many tries. Wait a bit and retry.")
                elif sha(code)==st.session_state.otp_hash:
                    st.session_state.authenticated=True; st.success("You’re in!")
                else:
                    st.session_state.otp_attempts+=1; st.error("That code didn’t match.")

# ════════════════════════════════════════════════
# 2. Review and edit details
# ════════════════════════════════════════════════
if st.session_state.authenticated:
    eid = int(st.session_state.employee_id)
    rec = df_master.loc[eid]
    st.title("✅ Step 1: Check each field")
    st.caption("If something’s wrong, pick “No” and add the right info.")

    fixes: dict[str, tuple] = {}
    with st.form("fields"):
        for col in df_master.columns:
            val = rec[col]
            show = val.strftime("%d/%m/%Y") if isinstance(val,pd.Timestamp) else ("<blank>" if pd.isna(val) else str(val))
            st.markdown(f"**{col.replace('_',' ').title()}**\nCurrent: {show}")
            if st.radio("Looks good?", ["Yes","No"], horizontal=True, key=f"r_{col}")=="No":
                if col in NAME_COLS:
                    new = st.text_input("Enter correct text", key=f"i_{col}")
                elif col in FORCE_DATE_COLS or pd.api.types.is_datetime64_any_dtype(df_master[col]):
                    base = val.to_pydatetime() if isinstance(val,pd.Timestamp) else datetime.date.today()
                    new = pd.to_datetime(st.date_input("Pick a date", value=base, key=f"i_{col}"))
                else:
                    opts = DROP_OPTIONS.get(col, [])
                    if show not in opts and show!="<blank>":
                        opts=[show]+opts
                    new = st.selectbox("Choose a value", opts, key=f"i_{col}")
                fixes[col]=(val,new)
            else:
                fixes[col]=(val,"(keep)")
        if st.form_submit_button("Next: review"):
            st.session_state.fixes=fixes; st.session_state.ready=True

    # ────────────────────────────────────────────
    # 3. One‑page summary
    # ────────────────────────────────────────────
    if st.session_state.get("ready"):
        ok={k:v[0] for k,v in st.session_state.fixes.items() if v[1]=="(keep)"}
        chg={k:v for k,v in st.session_state.fixes.items() if v[1]!="(keep)"}
        st.title("📝 Step 2: Confirm & submit")
        st.subheader("No change needed")
        for f,v in ok.items():
            st.write(f"• {f.replace('_',' ').title()}: {('<blank>' if pd.isna(v) else v.strftime('%d/%m/%Y') if isinstance(v,pd.Timestamp) else v)}")
        st.subheader("You updated")
        for f,(old,new) in chg.items():
            old_s='<blank>' if pd.isna(old) else old.strftime('%d/%m/%Y') if isinstance(old,pd.Timestamp) else old
            new_s=new.strftime('%d/%m/%Y') if isinstance(new,pd.Timestamp) else new
            st.write(f"• {f.replace('_',' ').title()} • was: {old_s} • now: {new_s}")
        if st.button("Submit"):
            now=datetime.datetime.now().isoformat(); row={"employee_id":eid,"email":st.session_state.email,"timestamp":now}
            for k,(ov,nv) in st.session_state.fixes.items():
                row[f"{k}_original"]='' if pd.isna(ov) else (ov.strftime('%d/%m/%Y') if isinstance(ov,pd.Timestamp) else ov)
                row[f"{k}_status"]='changed' if nv!="(keep)" else 'ok'
                row[f"{k}_new"]='' if nv=="(keep)" else (nv.strftime('%d/%m/%Y') if isinstance(nv,pd.Timestamp) else nv)
            save_row(row)
            st.success("All set – thank you!"); st.balloons(); st.session_state.clear()
