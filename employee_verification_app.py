"""
Employee Data Verification Portal (🎯 one question at a time)
===========================================================
Flow
----
1. Email + OTP login
2. For **each field** we ask:
   “Is *<field>* correctly filled in our records?”
   * If **Yes** → immediately move to next field.
   * If **No** → show the proper widget (text, date picker, dropdown). After saving, move on.
3. After last field: simple “Thank you!” message. No long summary.
"""

# ────────────────────────────────────────────────
# Imports & basic config
# ────────────────────────────────────────────────
import hashlib, random, string, time, datetime
from pathlib import Path
import pandas as pd, streamlit as st
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib, gspread
from google.oauth2.service_account import Credentials

ALLOWED_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com"}
OTP_VALID_SEC, RESEND_COOLDOWN_SEC, MAX_TRIES = 300, 30, 3
EXCEL = "Employee Master IT 2.0.xlsx"
SHEET_NAME, LOCAL_CSV = "Verified Corrections Log", "verified_corrections_log.csv"

NAME_COLS = {
    "employee_first_name", "employee_middle_name", "employee_last_name", "employee_father_name"
}
FORCE_DATE = {"date_of_substantive_entry"}

EMAIL_ADDRESS = st.secrets["EMAIL_ADDRESS"]
EMAIL_PASSWORD = st.secrets["EMAIL_PASSWORD"]

# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────
@st.cache_data
def load_master():
    df = pd.read_excel(EXCEL)
    return df.set_index("employee_id")

df_master = load_master()

DROP_OPTIONS = {}
for c in df_master.columns:
    if c in NAME_COLS or c in FORCE_DATE: continue
    if pd.api.types.is_datetime64_any_dtype(df_master[c]): continue
    vals = df_master[c].fillna("").astype(str).str.strip().unique().tolist()
    DROP_OPTIONS[c] = sorted(v for v in vals if v)

mk_otp = lambda n=6: "".join(random.choices(string.digits,k=n))
sha = lambda s: hashlib.sha256(s.encode()).hexdigest()

def send_code(email, code):
    msg = MIMEMultipart(); msg["From"], msg["To"] = EMAIL_ADDRESS, email
    msg["Subject"] = "Your one‑time code"
    msg.attach(MIMEText(f"Your code is {code}. It works for 5 minutes.","plain"))
    with smtplib.SMTP("smtp.gmail.com",587) as s:
        s.starttls(); s.login(EMAIL_ADDRESS,EMAIL_PASSWORD); s.sendmail(EMAIL_ADDRESS,email,msg.as_string())

def gs():
    creds = Credentials.from_service_account_info(st.secrets["gspread_service_account"])
    return gspread.authorize(creds).open(SHEET_NAME).sheet1

def already_done(emp):
    try:
        return str(emp) in gs().col_values(1)
    except Exception:
        p=Path(LOCAL_CSV); return p.exists() and str(emp) in pd.read_csv(p,usecols=["employee_id"],dtype=str)["employee_id"].tolist()

def save_log(row):
    try: gs().append_row([str(row[k]) for k in row])
    except Exception:
        p=Path(LOCAL_CSV); pd.DataFrame([row]).to_csv(p,mode="a",header=not p.exists(),index=False)

# ────────────────────────────────────────────────
# Session state defaults
# ────────────────────────────────────────────────
for k,v in {
    "otp_hash":"","otp_time":0.0,"otp_sent":False,"tries":0,"auth":False,
    "email":"","emp_id":"","field_idx":0,"answers":{},"done":False
}.items(): st.session_state.setdefault(k,v)

FIELDS = list(df_master.columns)

# ════════════════════════════════════════════════
# 1. Login step
# ════════════════════════════════════════════════
if not st.session_state.auth:
    st.title("Employee data check")
    with st.form("login"):
        email = st.text_input("Email (Gmail / Yahoo / Outlook)",value=st.session_state.email)
        emp = st.text_input("Employee ID",value=st.session_state.emp_id)
        if st.form_submit_button("Send code"):
            if not any(email.lower().endswith("@"+d) for d in ALLOWED_EMAIL_DOMAINS):
                st.error("Use Gmail, Yahoo or Outlook address.")
            elif not emp.isdigit() or int(emp) not in df_master.index:
                st.error("Employee ID not found.")
            elif already_done(int(emp)):
                st.error("You already verified. Contact HQ to reopen.")
            elif time.time()-st.session_state.otp_time<RESEND_COOLDOWN_SEC:
                st.warning("Wait a few seconds and try again.")
            else:
                code = mk_otp(); send_code(email,code)
                st.session_state.update({"otp_hash":sha(code),"otp_time":time.time(),"otp_sent":True,"email":email.strip(),"emp_id":emp.strip(),"tries":0})
                st.success("Code sent!")
    if st.session_state.otp_sent:
        code = st.text_input("Enter code",max_chars=6)
        if st.button("Log in"):
            if time.time()-st.session_state.otp_time>OTP_VALID_SEC:
                st.error("Code expired."); st.session_state.otp_sent=False
            elif st.session_state.tries>=MAX_TRIES:
                st.error("Too many tries. Wait and retry.")
            elif sha(code)==st.session_state.otp_hash:
                st.session_state.auth=True
            else:
                st.session_state.tries+=1; st.error("Code didn’t match.")

# ════════════════════════════════════════════════
# 2. One‑question‑at‑a‑time wizard
# ════════════════════════════════════════════════
if st.session_state.auth and not st.session_state.done:
    eid=int(st.session_state.emp_id); row=df_master.loc[eid]
    idx=st.session_state.field_idx
    if idx>=len(FIELDS):
        # all done
        data_row={"employee_id":eid,"email":st.session_state.email,"timestamp":datetime.datetime.now().isoformat()}
        for col,(orig,new) in st.session_state.answers.items():
            data_row[f"{col}_original"]= '' if pd.isna(orig) else (orig.strftime('%d/%m/%Y') if isinstance(orig,pd.Timestamp) else orig)
            data_row[f"{col}_status"]='changed' if new is not None else 'ok'
            data_row[f"{col}_new"]='' if new is None else (new.strftime('%d/%m/%Y') if isinstance(new,pd.Timestamp) else new)
        save_log(data_row)
        st.session_state.done=True
        st.success("Thank you! Your response is recorded."); st.balloons()
        st.stop()

    col=FIELDS[idx]
    orig=row[col]
    disp = orig.strftime('%d/%m/%Y') if isinstance(orig,pd.Timestamp) else ('<blank>' if pd.isna(orig) else str(orig))
    st.header(f"{idx+1}/{len(FIELDS)} • {col.replace('_',' ').title()}")
    st.write(f"Current value: **{disp}**")
    choice=st.radio(f"Is *{col.replace('_',' ').title()}* correct?",["Yes","No"],horizontal=True,key=f"q{idx}")

    if choice=="Yes":
        st.session_state.answers[col]=(orig,None)
        st.session_state.field_idx+=1
        st.experimental_rerun()
    else:
        # show corrector widget
        if col in NAME_COLS:
            new_val=st.text_input("Type the correct text")
        elif col in FORCE_DATE or pd.api.types.is_datetime64_any_dtype(df_master[col]):
            base=orig.to_pydatetime() if isinstance(orig,pd.Timestamp) else datetime.date.today()
            new_val=pd.to_datetime(st.date_input("Pick the right date",value=base))
        else:
            opts=DROP_OPTIONS.get(col,[])
            if disp not in opts and disp!="<blank>":
                opts=[disp]+opts
            new_val=st.selectbox("Choose the right value",opts)
        if st.button("Save and continue") and (new_val!=""   ):
            st.session_state.answers[col]=(orig,new_val)
            st.session_state.field_idx+=1
            st.experimental_rerun()
