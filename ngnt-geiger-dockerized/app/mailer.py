"""Mail through the SMTP server the admin set on the admin page. Standard library only: the web app
sends the reset mail with it, the MQTT subscriber (scripts/pm2/, `app/` mounted read-only at
/opt/app) the dose-rate alert."""

import logging
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid, parseaddr

logger = logging.getLogger(__name__)


def send_mail(to: str, subject: str, body: str, settings: dict) -> bool:
    smtp_host = settings.get('smtp_host', '')
    if not smtp_host:
        return False

    smtp_port = int(settings.get('smtp_port', '587'))
    smtp_user = settings.get('smtp_user', '')
    smtp_password = settings.get('smtp_password', '')
    smtp_tls = settings.get('smtp_tls', '1') == '1'
    site_name = os.environ.get('SITE_NAME', 'NGNT Geiger Counter')
    # The From field may be saved empty: the SMTP user sends then.
    from_name, sender = parseaddr(settings.get('smtp_from') or smtp_user)
    domain = sender.rpartition('@')[2].lower() if '@' in sender else ''
    if '.' not in domain or domain.endswith('.local') or domain in ('example.com', 'example.org', 'example.net'):
        logger.error('mail not sent: From address %r is not a real address', sender)
        return False

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = formataddr((from_name or site_name, sender))
    msg['To'] = to
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=domain)
    msg['Auto-Submitted'] = 'auto-generated'

    ehlo_name = settings.get('smtp_ehlo_name') or domain
    context = ssl.create_default_context()
    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, local_hostname=ehlo_name, timeout=10, context=context)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, local_hostname=ehlo_name, timeout=10)
            if smtp_tls:
                server.starttls(context=context)
        if smtp_user:
            server.login(smtp_user, smtp_password)
        server.sendmail(sender, [to], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        logger.error('mail send failed: %s', e)
        return False
