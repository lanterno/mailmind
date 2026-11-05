"""The fake SMTP server behaves like a submission server to the app's own smtplib code: TLS before
AUTH, AUTH before MAIL, dot-stuffing undone, and scripted refusals and drops that reach the app as
the errors smtpsend.sanitize_smtp_error knows."""
import smtplib

import pytest

from fakes.smtp_server import FakeSMTPServer

ADA = "ada@example.test"
RAW = (b"From: Ada <ada@example.test>\r\nTo: Lin <lin@example.test>\r\nSubject: Hi\r\n"
       b"Message-ID: <hi-1@example.test>\r\n\r\nLine one\r\n.leading dot\r\n")


@pytest.fixture
def smtp():
    with FakeSMTPServer() as srv:
        srv.add_user(ADA, "app-password")
        yield srv


def acct(srv, password="app-password"):
    return {"email": ADA, "smtp_host": "127.0.0.1", "smtp_port": srv.port, "smtp_starttls": True,
            "credential": {"plain": password}}


def test_the_app_sends_over_starttls_with_a_login(smtp, data_dir):
    import smtpsend
    smtpsend.send(acct(smtp), RAW, ["lin@example.test", "noor@studio.example.test"])
    [m] = smtp.messages
    assert (m.user, m.mail_from, m.rcpts) == (ADA, ADA, ["lin@example.test", "noor@studio.example.test"])
    assert m.data == RAW                                   # the leading dot survives the round trip
    assert [c["command"] for c in smtp.commands] == ["EHLO", "STARTTLS", "EHLO", "AUTH", "MAIL", "RCPT",
                                                     "RCPT", "DATA", "QUIT"]
    assert "app-password" not in str(smtp.commands)


def test_auth_before_starttls_is_refused(smtp):
    s = smtplib.SMTP("127.0.0.1", smtp.port, timeout=10)
    try:
        s.ehlo()
        assert "auth" not in s.esmtp_features
        assert s.docmd("AUTH", "PLAIN AGFkYUBleGFtcGxlLnRlc3QAYXBwLXBhc3N3b3Jk")[0] == 530
        assert s.docmd("MAIL", "FROM:<ada@example.test>")[0] == 530
    finally:
        s.close()


def test_a_wrong_password_is_an_authentication_error(smtp, data_dir):
    import smtpsend
    with pytest.raises(smtplib.SMTPAuthenticationError) as e:
        smtpsend.send(acct(smtp, "wrong"), RAW, ["lin@example.test"])
    assert smtpsend.sanitize_smtp_error(e.value) == smtpsend.sanitize_smtp_error(
        smtplib.SMTPAuthenticationError(535, b"x"))
    assert smtp.messages == []


def test_scripted_refusals_reach_the_app(smtp, data_dir):
    import smtpsend
    smtp.fail("RCPT", "550 5.1.1 no such user", when=lambda arg: "nobody" in arg)
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        smtpsend.send(acct(smtp), RAW, ["nobody@example.test"])
    smtp.fail("DATA-END", "554 5.7.1 message rejected")
    with pytest.raises(smtplib.SMTPDataError):
        smtpsend.send(acct(smtp), RAW, ["lin@example.test"])
    assert smtp.messages == []
    smtpsend.send(acct(smtp), RAW, ["lin@example.test"])     # each fault fires once
    assert len(smtp.messages) == 1


def test_a_drop_after_data_leaves_the_client_unsure(smtp, data_dir):
    import smtpsend
    smtp.drop_after_data()
    with pytest.raises(smtpsend.Unanswered):
        smtpsend.send(acct(smtp), RAW, ["lin@example.test"])
    assert len(smtp.messages) == 1                        # it was delivered all the same


def test_a_drop_before_mail_sends_nothing(smtp, data_dir):
    import smtpsend
    smtp.drop("MAIL")
    with pytest.raises(smtplib.SMTPServerDisconnected):
        smtpsend.send(acct(smtp), RAW, ["lin@example.test"])
    assert smtp.messages == []


def test_on_message_sees_each_accepted_message(smtp, data_dir):
    import smtpsend
    seen = []
    smtp.on_message = seen.append
    smtpsend.send(acct(smtp), RAW, ["lin@example.test"])
    assert [d.rcpts for d in seen] == [["lin@example.test"]]


def test_a_5xx_is_permanent_and_a_4xx_or_a_drop_is_not(smtp, data_dir):
    import smtpsend

    def raised(**fault):
        if fault:
            smtp.fail(**fault)
        try:
            smtpsend.send(acct(smtp), RAW, ["lin@example.test"])
        except Exception as e:
            return e
        raise AssertionError("it sent")

    assert smtpsend.permanent(raised(command="RCPT", reply="550 5.1.1 no such user"))
    assert smtpsend.permanent(raised(command="DATA-END", reply="554 5.7.1 message rejected"))
    assert smtpsend.permanent(raised(command="MAIL", reply="553 5.7.1 sender not allowed"))
    assert not smtpsend.permanent(raised(command="RCPT", reply="451 4.3.0 try again later"))
    assert not smtpsend.permanent(raised(command="DATA-END", reply="452 4.3.1 out of storage"))
    smtp.drop("MAIL")
    assert not smtpsend.permanent(raised())
    with pytest.raises(smtplib.SMTPAuthenticationError) as e:
        smtpsend.send(acct(smtp, "wrong"), RAW, ["lin@example.test"])
    assert smtpsend.permanent(e.value)


def test_recipients_refused_are_permanent_only_when_every_one_is_5xx():
    import smtpsend
    refused = smtplib.SMTPRecipientsRefused
    assert smtpsend.permanent(refused({"a@example.test": (550, b"no"), "b@example.test": (553, b"no")}))
    assert not smtpsend.permanent(refused({"a@example.test": (550, b"no"), "b@example.test": (450, b"busy")}))
    assert not smtpsend.permanent(refused({}))
    assert not smtpsend.permanent(ConnectionRefusedError())
    assert not smtpsend.permanent(TimeoutError())
