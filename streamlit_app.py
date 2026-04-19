import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import streamlit as st
from scraper import format_output, scrape_all

st.set_page_config(page_title="Telegraph India Racing Tips", layout="centered")
st.title("Telegraph India Racing Tips")

if st.button("Scrape Today's Tips", type="primary"):
    with st.spinner("Scraping Telegraph India..."):
        meetings = scrape_all()

    output = format_output(meetings)
    if not output:
        st.warning("No upcoming meetings with picks were found.")
    else:
        html_lines = []
        for line in output.split("\n"):
            stripped = line.rstrip()
            is_meeting = bool(stripped) and stripped == stripped.upper() and not stripped.startswith("R")
            is_tagged = stripped.endswith(" NAP") or stripped.endswith(" NB")
            if is_meeting or is_tagged:
                html_lines.append(f"<strong>{line}</strong>")
            else:
                html_lines.append(line)
        html_output = "<br>".join(html_lines)

        st.markdown(html_output, unsafe_allow_html=True)

        st.divider()
        st.subheader("Copy for WordPress")
        st.code(html_output, language="html")

        try:
            app_password = st.secrets["GMAIL_APP_PASSWORD"]
            email_from = st.secrets["EMAIL_FROM"]
            email_to = st.secrets["EMAIL_TO"]
        except (KeyError, FileNotFoundError):
            app_password = email_from = email_to = ""

        if app_password and email_from and email_to:
            with st.spinner("Sending email..."):
                msg = MIMEMultipart("alternative")
                msg["Subject"] = "Telegraph India Racing Tips"
                msg["From"] = email_from
                msg["To"] = email_to
                msg.attach(MIMEText(output, "plain"))
                msg.attach(MIMEText(html_output, "html"))

                try:
                    with smtplib.SMTP("smtp.gmail.com", 587) as server:
                        server.starttls()
                        server.login(email_from, app_password)
                        server.sendmail(email_from, email_to.split(","), msg.as_string())
                    st.success(f"Email sent to {email_to}")
                except Exception as e:
                    st.error(f"Failed to send email: {e}")
        else:
            st.warning("Email credentials not configured in .streamlit/secrets.toml")
