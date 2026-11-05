Fixed a crash when opening Telegram, Slack, or Assistant email settings. Their credential fields are now registered
with the shared form, so tokens and the mailbox password can be saved, replaced, or cleared without exposing stored
values. Cancel and Undo preserve the saved credentials.
