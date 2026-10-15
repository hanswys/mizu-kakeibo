# Job Tracker Contract

**Status:** Draft. Only the sampled layouts are recorded. The remaining sections are written by J0-2 in the [roadmap](ROADMAP.md).

This document will be the source of truth for the job tracker's retrieval, analyser input and output, normalization of company names and job titles, matching, status update, and persistence rules. Product scope belongs in the [MVP](MVP.md). The shared Gmail adapter and `inbox-message/v0` envelope are defined in the [expense contract](../expenses/inbox-expense-contract.md) and are referenced, not copied.

No real email content, names, or identifiers belong in this document.

## Sampled layouts

Sampled on 2026-10-14 for J0-1 through read-only calls to the claude.ai Gmail connector, over inbox messages received on or after 2026-04-14. 27 LinkedIn and Jobstreet messages were decoded in full; counts come from subject searches. Employer emails were identified from search results only. Everything below is structure and anonymized templates.

### Gmail connector boundary

The connector differs from the boundary first recorded in the expense contract §1. The shared adapter's handling of it is defined in the expense contract's [connector section](../expenses/inbox-expense-contract.md#claudeai-gmail-connector); the observations below are the sampling record.

- Available tools: `search_threads`, `get_thread`, `get_message`. The `search_email_ids` and `read_email(format="full")` tools recorded in the expense contract were not available.
- `get_message` with `FULL_CONTENT` returns a flat object: `id`, `threadId`, `internalDate`, `date`, `sender`, `subject`, `snippet`, `labelIds`, `plaintextBody`, `htmlBody`, `toRecipients`, `sizeEstimate`, `historyId`, `viewUrl`. It has no MIME tree and no header list, so the normalizer cannot read it.
- `get_message` with `RAW` returns `raw`, a base64url RFC 822 message, plus `id`, `threadId`, `internalDate`, and `labelIds`. The MIME tree and headers in the shape the normalizer expects can be rebuilt from it.
- `search_threads` returns threads, not messages. Gmail groups same-subject notifications into one thread (up to 17 messages observed), and a search result lists only some of a thread's messages. Message IDs must be enumerated with `get_thread`.

### MIME structure

Every decoded message was `multipart/alternative` with one `text/plain` and one `text/html` part, encoded quoted-printable (one marketing message used 8-bit), with no attachments. No HTML-only message was found.

The two alternatives are not always equivalent:

- LinkedIn viewed and rejected notifications have a stub plain-text part: a headline (for rejections, `Your update from {company}`) and the footer. The job title, job card, and rejection wording exist only in the HTML part. 14 of 43 LinkedIn status emails in the window have this layout.
- Plain-text parts are 5 to 8 times longer than the converted HTML because every link is written out as a long tracking URL. Converted HTML was under 3,600 characters for every sample; the longest plain-text body was about 21,400 characters, close to the 24,000-character envelope limit.

Rebuilt from `RAW` and passed to the current normalizer unchanged, every sample normalized without error as `text_plain` and none was truncated. Because the normalizer prefers non-empty plain text, LinkedIn viewed envelopes contain no job title and LinkedIn rejection envelopes contain no rejection wording.

Minor text artifacts: Jobstreet plain text contains a template token (`%%str_to_replace_open_tracking%%`) and letter-spaced dates; Jobstreet HTML separates date characters with zero-width spaces, which NFKC normalization and the control-character filter keep; one marketing plain-text part contains undecoded `&zwnj;` entities.

### LinkedIn and Jobstreet status layouts

All LinkedIn and Jobstreet status emails came from two senders: `jobs-noreply@linkedin.com` and `noreply@e.jobstreet.com`.

| Sender | Subject template | Status | Company | Job title | Count |
| --- | --- | --- | --- | --- | --- |
| LinkedIn | `{first_name}, your application was sent to {company}` | `applied` | subject and body | body | 29 |
| LinkedIn | `Your application was viewed by {company}` | `viewed` | subject and HTML body | HTML body only | 6 |
| LinkedIn | `Your application to {title} at {company}` | `rejected` | subject and HTML body | subject and HTML body | 8 |
| Jobstreet | `Application submitted - verify key information to help it stand out`; earlier `Your application was successfully submitted` | `applied` | body only | body only | 28 |
| Jobstreet | `{company} has viewed your application for {title}` | `viewed` | subject and body | subject and body | 11 |
| Jobstreet | `Application update for {title} at {company}` | `rejected` ("unlikely to progress further") | subject and body | subject and body | 8 |
| Jobstreet | `Hi {first_name}, the {title} job with {company} has closed` | none: the listing expired and the employer keeps the application | subject and body | subject and body | 7 |
| Jobstreet | `{first_name}, new activity in jobs you applied for` | none: a digest of several applications, each with a platform state such as `Reviewing applications` or `Job no longer advertised` | per item | per item | 3 |

Neither platform sent an interview, offer, assessment, or withdrawal email in the window.

Body layout for every status type: a headline, a job card with `{title}`, `{company}`, location, and `Applied on {date}`, then a list of other recommended jobs with their own titles and companies. LinkedIn viewed notices also name the job poster and people at the company. Jobstreet rejections add screening-question feedback.

### Employer and hiring-system emails

Interview invitations and most outcomes came from outside LinkedIn and Jobstreet:

- Employer recruiting addresses: an interview invitation that writes the role as `{group_name}-{title}-{country}`, and interview scheduling threads with individual recruiters.
- Company career portals and hiring systems, such as Workable, Ashby, and in-house career sites: application confirmations, rejections (`Thank you for applying to {title}! While we cannot move forward at this time...`, `{company} application: Status update`), interview reminders, and post-interview surveys.
- Other job boards, such as Indeed: application confirmations.
- Text-message forwarding services: an interview request delivered as a forwarded text message.

At least two employer emails concern roles applied for through LinkedIn. No offer email was found in the window.

### Non-application layouts

| Sender | Subject template | Notes |
| --- | --- | --- |
| `jobalerts-noreply@linkedin.com` | `{title} at {company}` | job alert digest; the subject resembles the LinkedIn rejection template |
| `jobs-noreply@linkedin.com` | `{company} hiring: {title}`, `{company} is hiring: {title}` | recommendations |
| `jobs-noreply@linkedin.com` | `{first_name}, apply now to '{title} at {company}'`, `{first_name}, your job's expiring on {date}: {title} at {company}` | saved-job reminders with a title and company but no application |
| `inmail-hit-reply@linkedin.com` | free text | recruiter outreach |
| `messages-noreply@linkedin.com` | `{name} sent you a message {n} days ago`, profile views | the message content is not included |
| `noreply@e.jobstreet.com` | `{title} [{match label}] + {n} new jobs - Job Alert from Jobstreet.com` | job alert |
| `noreply@e.jobstreet.com` | `{company} is still accepting applications - don't forget to submit yours.` | unfinished application; the body shows a shortened company name |
| `noreply@email.jobstreet.com`, `noreply@research.jobstreet.com` | various | marketing, career advice, surveys |

Other LinkedIn senders seen carried no application status: `invitations@`, `groups-noreply@`, `notifications-noreply@`, `newsletters-noreply@`, `updates-noreply@`, `jobs-listings@`, `hit-reply@`, `em.linkedin.com`, and individual staff addresses.

### Name consistency

- Within one platform, company and job title strings were identical across every email for the same application, including legal-form spelling, case, and punctuation. One title differed only by a doubled internal space.
- Legal-form spelling differs between companies (`Sdn Bhd`, `Sdn. Bhd.`, `SDN. BHD.`, `sdn bhd`), not within one company's emails.
- Across sources, names differed: an employer interview invitation used the group name instead of the brand, and combined title and country into one string.

### Observed sequences

- LinkedIn: applied, then rejected 3 days later (several); applied, then viewed (four, one within 4 hours).
- LinkedIn: viewed or rejected for roles applied for before 2026-04-14, with no applied email in the window.
- LinkedIn then employer: applied, viewed, then an employer interview invitation the next day.
- Three different roles at one company on LinkedIn; two different roles at one company on Jobstreet on the same day.
- Jobstreet: submitted, viewed, rejected; submitted, viewed, closed, then a digest showing `Reviewing applications`.
- Jobstreet: submitted, closed, viewed, rejected (viewed arrived after closed).
- Jobstreet: submitted, rejected, submitted again for the same title and company, rejected again.
