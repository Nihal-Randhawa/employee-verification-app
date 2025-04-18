# Streamlit App: Employee Data Verification with Email OTP

import streamlit as st
import pandas as pd
import smtplib
import random
import string
import time
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import socket
import platform
from google.oauth2.service_account import Credentials
import gspread

# Constants
ALLOWED_EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com"]
OTP_VALIDITY_SECONDS = 300  # 5 minutes

EMAIL_ADDRESS = st.secrets["EMAIL_ADDRESS"]
EMAIL_PASSWORD = st.secrets["EMAIL_PASSWORD"]

# Session state setup
if "otp" not in st.session_state:
    st.session_state.otp = ""
if "otp_time" not in st.session_state:
    st.session_state.otp_time = 0
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "email" not in st.session_state:
    st.session_state.email = ""
if "employee_id" not in st.session_state:
    st.session_state.employee_id = ""

# Load employee data
df = pd.read_excel("Employee Master IT 2.0.xlsx")
df.set_index("employee_id", inplace=True)

# Dropdown fields
dropdown_fields = {
    'employee_community': sorted(df['employee_community'].dropna().unique().tolist()),
    'marital_status': sorted(df['marital_status'].dropna().unique().tolist()),
    'recruitment_mode': sorted(df['recruitment_mode'].dropna().unique().tolist()),
    'cadre': sorted(df['cadre'].dropna().unique().tolist()),
    'group_post': sorted(df['group_post'].dropna().unique().tolist()),
    'employee_designation': sorted(df['employee_designation'].dropna().unique().tolist()),
    'office_of_working': sorted(df['office_of_working'].dropna().unique().tolist()),
    'selected_community': sorted(df['selected_community'].dropna().unique().tolist()),
}

# Email sender function
def send_otp(email, otp):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = email
    msg['Subject'] = 'Your OTP for Employee Data Verification'
    body = f"Your OTP is: {otp}\nIt is valid for 5 minutes."
    msg.attach(MIMEText(body, 'plain'))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    server.sendmail(EMAIL_ADDRESS, email, msg.as_string())
    server.quit()

# Login and OTP
if not st.session_state.authenticated:
    st.title("🔐 Employee Data Verification Portal")
    email = st.text_input("Enter your Email")
    emp_id = st.text_input("Enter your Employee ID")

    if st.button("Send OTP"):
        if not any(email.endswith("@" + domain) for domain in ALLOWED_EMAIL_DOMAINS):
            st.error("Please use Gmail, Yahoo, or Outlook only.")
        elif not emp_id.isdigit() or int(emp_id) not in df.index:
            st.error("Invalid Employee ID.")
        else:
            otp = ''.join(random.choices(string.digits, k=6))
            st.session_state.otp = otp
            st.session_state.otp_time = time.time()
            st.session_state.email = email
            st.session_state.employee_id = emp_id
            send_otp(email, otp)
            st.success("OTP sent! Please check your email.")

    otp_input = st.text_input("Enter the OTP sent to your email")
    if st.button("Verify OTP"):
        if otp_input == st.session_state.otp and (time.time() - st.session_state.otp_time) <= OTP_VALIDITY_SECONDS:
            st.session_state.authenticated = True
            st.success("OTP verified. Proceed below.")
        else:
            st.error("Invalid or expired OTP.")

# Main verification form
if st.session_state.authenticated:
    emp_id = int(st.session_state.employee_id)
    employee_data = df.loc[emp_id]
    corrections = {}
    st.title("📋 Verify Your Details")

    for col in df.columns:
        current_val = employee_data[col]
        if pd.isna(current_val):
            if col == 'employee_middle_name':
                current_val_display = "Blank, that is no middle name"
            else:
                current_val_display = "Not provided"
        elif isinstance(current_val, pd.Timestamp):
            current_val_display = current_val.strftime('%d/%m/%Y')
        else:
            current_val_display = current_val

        st.markdown(f"**{col.replace('_', ' ').title()}**: {current_val_display}")
        prompt = "Is this correct?" if col == 'employee_middle_name' else f"Is this correct? ({col})"
        confirm = st.radio(prompt, ["Yes", "No"], key=col)
        if confirm == "No":
            if col in dropdown_fields:
                new_val = st.selectbox(f"Select correct value for {col}", dropdown_fields[col], key="input_" + col)
            else:
                new_val = st.text_input(f"Enter correct value for {col}", key="input_" + col)
            corrections[col] = (current_val, new_val)
        else:
            corrections[col] = (current_val, "(confirmed)")

    if st.button("Review Summary"):
        verified = {k: v[0] for k, v in corrections.items() if v[1] == "(confirmed)"}
        corrected = {k: v for k, v in corrections.items() if v[1] != "(confirmed)"}

        st.subheader("✅ Summary of Your Review")
        st.write("Below is a summary of the information you have reviewed.")

        st.markdown("### ✅ Fields You Confirmed as Correct")
        for field, value in verified.items():
            formatted = value.strftime('%d/%m/%Y') if isinstance(value, (pd.Timestamp, datetime.date)) else value
            st.markdown(f"- **{field.replace('_', ' ').title()}**: {formatted}")

        st.markdown("### ✏️ Fields You Marked for Correction")
        for field, (old_val, new_val) in corrected.items():
            old_fmt = old_val.strftime('%d/%m/%Y') if isinstance(old_val, (pd.Timestamp, datetime.date)) else old_val
            new_fmt = new_val.strftime('%d/%m/%Y') if isinstance(new_val, (pd.Timestamp, datetime.date)) else new_val
            st.markdown(f"- **{field.replace('_', ' ').title()}**\n    - Original: `{old_fmt}`\n    - Suggested Correction: `{new_fmt}`")

        if st.button("Submit Confirmation"):
            summary = {
                "employee_id": emp_id,
                "email": st.session_state.email,
                "timestamp": datetime.datetime.now().isoformat(),
                "ip": socket.gethostbyname(socket.gethostname()),
                "device": platform.platform(),
            }
            for k, v in corrections.items():
                summary[k + "_original"] = v[0].strftime('%d/%m/%Y') if isinstance(v[0], (pd.Timestamp, datetime.date)) else v[0]
                summary[k + "_status"] = "corrected" if v[1] != "(confirmed)" else "confirmed"
                summary[k + "_new"] = v[1].strftime('%d/%m/%Y') if isinstance(v[1], (pd.Timestamp, datetime.date)) else v[1] if v[1] != "(confirmed)" else ""

            summary_df = pd.DataFrame([summary])
            try:
                creds = Credentials.from_service_account_info(st.secrets["gspread_service_account"])
                client = gspread.authorize(creds)
                sheet = client.open("Verified Corrections Log").sheet1
                sheet.append_row(summary_df.iloc[0].astype(str).tolist())
            except Exception as e:
                st.warning(f"⚠️ Could not write to Google Sheet: {e}. Data saved locally instead.")
                try:
                    old = pd.read_csv("verified_corrections_log.csv")
                    new_df = pd.concat([old, summary_df], ignore_index=True)
                except FileNotFoundError:
                    new_df = summary_df
                new_df.to_csv("verified_corrections_log.csv", index=False)

            st.success(f"✅ Successfully submitted details for Employee ID {emp_id}. Thank you for verifying your information!")
            st.balloons()
            st.markdown("---")
            st.markdown("You may now close this window.")
