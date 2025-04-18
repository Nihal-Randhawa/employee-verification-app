"""
Employee Data Verification Portal
=================================
Streamlit app that lets employees confirm or correct demographic details.
Re‑written for:
• One‑submission‑per‑employee guard  • Mobile‑first UX wizard  • Cached data load
• OTP throttling / resend cooldown   • Sanitised inputs  • Robust logging fallback
"""

import hashlib
import random
import string
import time
import datetime
from pathlib import Path
import socket
import platform

import pandas as pd
import streamlit as st
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
from google.oauth2.service_account import Credentials
import gspread

# ────────────────────────────────────────────────────────────────────────────────
# Configuration & Constants
# ────────────────────────────────────────────────────────────────────────────────
ALLOWED_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com"}
OTP_VALID_FOR_SEC = 300
RESEND_COOLDOWN_SEC = 30
MAX_OTP_ATTEMPTS = 3

EMAIL_ADDRESS = st.secrets["EMAIL_ADDRESS"]
EMAIL_PASSWORD = st.secrets["EMAIL_PASSWORD"]

# ────────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────────
@st.cache_data
def load_master() -> pd.DataFrame:
    df = pd.read_excel("Employee Master IT 2.0.xlsx")
    return df.set_index("employee_id")

df_master = load_master()

# Pre‑compute dropdown options
DROP_OPTIONS = {
    col: sorted(df_master[col].dropna().unique().tolist())
    for col in [
        "employee_community",
        "marital_status",
        "recruitment_mode",
        "cadre",
        "group_post",
        "employee_designation",
        "office_of_working",
        "selected_community",
    ]
}

def generate_otp(n: int = 6) -> str:
    return "".join(random.choices(string.digits, k=n))

def hash_str(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def send_otp(email: str, otp: str) -> None:
    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = email
    msg["Subject"] = "Your OTP for Employee Data Verification"
    msg.attach(MIMEText(f"Your OTP is: {otp}\nIt is valid for 5 minutes.", "plain"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, email, msg.as_string())

def get_gsheet(sheet_name: str):
    creds = Credentials.from_service_account_info(st.secrets["gspread_service_account"])
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1

def already_submitted(emp_id: int) -> bool:
    try:
        sheet = get_gsheet("Verified Corrections Log")
        return str(emp_id) in sheet.col_values(1)
    except Exception:
        # Fallback to local CSV check
        f = Path("verified_corrections_log.csv")
        if not f.exists():
            return False
        return str(emp_id) in pd.read_csv(f, usecols=["employee_id"], dtype=str)["employee_id"].tolist()

# ────────────────────────────────────────────────────────────────────────────────
# Session State defaults
# ────────────────────────────────────────────────────────────────────────────────
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

# ────────────────────────────────────────────────────────────────────────────────
# 1. Login + OTP step
# ────────────────────────────────────────────────────────────────────────────────
if not st.session_state.authenticated:
    st.title("🔐 Employee Data Verification Portal")
    with st.form("login_form", clear_on_submit=False):
        email = st.text_input("Official / Personal Email", value=st.session_state.email)
        emp_id = st.text_input("Employee ID", value=st.session_state.employee_id)
        send_btn = st.form_submit_button("Send OTP")

    if send_btn:
        if not any(email.lower().endswith("@" + d) for d in ALLOWED_EMAIL_DOMAINS):
            st.error("Please use Gmail, Yahoo, or Outlook only.")
        elif not emp_id.isdigit() or int(emp_id) not in df_master.index:
            st.error("Invalid Employee ID.")
        elif already_submitted(int(emp_id)):
            st.error("Our records show you have already submitted. Contact HR to reopen edits.")
        elif time.time() - st.session_state.otp_time < RESEND_COOLDOWN_SEC:
            st.warning("Please wait a few seconds before resending the OTP.")
        else:
            otp_plain = generate_otp()
            st.session_state.otp_hash = hash_str(otp_plain)
            st.session_state.otp_time = time.time()
            st.session_state.otp_attempts = 0
            st.session_state.otp_sent = True
            st.session_state.email = email.strip()
            st.session_state.employee_id = emp_id.strip()
            send_otp(email, otp_plain)
            st.success("OTP sent! Check your inbox.")

    if st.session_state.otp_sent:
        with st.form("otp_form"):
            otp_in = st.text_input("Enter OTP", max_chars=6)
            verify_btn = st.form_submit_button("Verify OTP")
        if verify_btn:
            if time.time() - st.session_state.otp_time > OTP_VALID_FOR_SEC:
                st.error("OTP expired. Click 'Send OTP' again.")
                st.session_state.otp_sent = False
            elif st.session_state.otp_attempts >= MAX_OTP_ATTEMPTS:
                st.error("Too many attempts. Wait 15 minutes and retry.")
            elif hash_str(otp_in) == st.session_state.otp_hash:
                st.session_state.authenticated = True
                st.success("Authenticated! Proceed below.")
            else:
                st.session_state.otp_attempts += 1
                st.error(f"Incorrect OTP. Attempt {st.session_state.otp_attempts}/{MAX_OTP_ATTEMPTS}")

# ────────────────────────────────────────────────────────────────────────────────
# 2. Verification wizard
# ────────────────────────────────────────────────────────────────────────────────
if st.session_state.authenticated:
    emp_id_int = int(st.session_state.employee_id)
    record = df_master.loc[emp_id_int]

    st.title("📋 Verify Your Details")
    st.caption("Tap field → confirm or correct → *Next*. Only one submission allowed.")

    corrections = {}
    with st.form("verify_form"):
        for col in df_master.columns:
            orig_val = record[col]
            if isinstance(orig_val, pd.Timestamp):
                disp_val = orig_val.strftime("%d/%m/%Y")
            elif pd.isna(orig_val) and col == "employee_middle_name":
                disp_val = "<blank>"
            else:
                disp_val = orig_val

            st.markdown(f"### {col.replace('_', ' ').title()}")
            st.markdown(f"Current value: **{disp_val}**")
            confirm = st.radio("Is this correct?", ["Yes", "No"], key=f"radio_{col}", horizontal=True)
            if confirm == "No":
                if col in DROP_OPTIONS:
                    new_val = st.selectbox("Select correct value", DROP_OPTIONS[col], key=f"input_{col}")
                elif isinstance(orig_val, pd.Timestamp):
                    default_date = orig_val.to_pydatetime() if not pd.isna(orig_val) else datetime.date.today()
                    new_dt = st.date_input("Choose date", value=default_date, key=f"input_{col}")
                    new_val = pd.to_datetime(new_dt)
                else:
                    new_val = st.text_input("Enter correct value", key=f"input_{col}")
                corrections[col] = (orig_val, new_val)
            else:
                corrections[col] = (orig_val, "(confirmed)")

        next_btn = st.form_submit_button("Review Summary")

    if next_btn:
        verified = {k: v[0] for k, v in corrections.items() if v[1] == "(confirmed)"}
        corrected = {k: v for k, v in corrections.items() if v[1] != "(confirmed)"}

        st.subheader("✅ Fields Confirmed")
        for f, v in verified.items():
            v_str = v.strftime("%d/%m/%Y") if isinstance(v, (pd.Timestamp, datetime.date)) else v
            st.markdown(f"• **{f.replace('_', ' ').title()}**: {v_str}")

        st.subheader("✏️ Fields Corrected")
        for f, (old
