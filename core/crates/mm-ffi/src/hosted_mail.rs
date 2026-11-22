//! Local-only MIME views for the encrypted HodHod mailbox.
use mm_content::mime;

#[allow(missing_debug_implementations, reason = "contains decrypted mailbox data")]
#[derive(uniffi::Record)]
pub struct HostedMailHeader {
    pub subject: String,
    pub sender: String,
    pub from_address: String,
    pub message_id: String,
    pub references: String,
}

#[allow(missing_debug_implementations, reason = "contains decrypted mailbox data")]
#[derive(uniffi::Record)]
pub struct HostedAttachment {
    pub part_id: u32,
    pub filename: String,
    pub size: u64,
}

#[allow(missing_debug_implementations, reason = "contains decrypted mailbox data")]
#[derive(uniffi::Record)]
pub struct HostedMailReading {
    pub text: String,
    pub reply_to: String,
    pub attachments: Vec<HostedAttachment>,
}

#[uniffi::export]
pub fn hosted_headers(raw: Vec<u8>) -> HostedMailHeader {
    let fields = mime::fields(&raw);
    HostedMailHeader {
        subject: fields.subject,
        sender: if fields.from_name.is_empty() { fields.from_addr.clone() } else { fields.from_name },
        from_address: fields.from_addr,
        message_id: fields.message_id,
        references: fields.refs,
    }
}

#[uniffi::export]
pub fn hosted_reading(raw: Vec<u8>) -> HostedMailReading {
    let parsed = mime::parse(&raw);
    let text = parsed
        .text
        .as_deref()
        .filter(|text| !text.trim().is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| mime::text_body(&raw));
    HostedMailReading {
        text,
        reply_to: parsed.reply_to,
        attachments: parsed
            .attachments
            .into_iter()
            .filter(|part| !part.is_inline)
            .map(|part| HostedAttachment { part_id: part.part_id, filename: part.filename, size: part.size as u64 })
            .collect(),
    }
}

#[uniffi::export]
pub fn hosted_attachment(raw: Vec<u8>, part_id: u32) -> Option<Vec<u8>> {
    mime::extract_part(&raw, part_id).map(|(bytes, _)| bytes.into_owned())
}
