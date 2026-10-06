# The assistant in chat rooms

Lens's assistant can answer in Slack, Discord, Telegram, Matrix, Mattermost, IRC, WhatsApp and the other chats
[Matterbridge](https://github.com/42wim/matterbridge) bridges. Say "Lens, what did we decide about the launch?" (or
"@Lens …") in a bridged room and it answers there, as it would in Chat.

## Set it up

1. Give Matterbridge an API account and put it in the gateway with your rooms:

   ```toml
   [api.lens]
   BindAddress="0.0.0.0:4242"
   Token="a-long-random-token"
   Buffer=1000

   [[gateway]]
   name="team"
   enable=true
     [[gateway.inout]]
     account="slack.work"
     channel="general"
     [[gateway.inout]]
     account="api.lens"
     channel="api"
   ```

2. In Lens, open **Settings → Chat rooms**, give the API's address (`http://matterbridge:4242` on the same Docker
   network) and its token, and turn on **Answer in chat rooms**. **Check the connection** says whether Matterbridge
   answers and the account is right.

That's all. Without an account given, it answers as the admin who turned it on.

## What it does

- **Who it answers.** Messages that start with its name or mention `@Lens` (or every message, with **Answers: every
  message**), in every gateway or only one, from everyone in the bridged rooms or only the chat usernames listed.
- **As whom.** It answers as one Lens account (**Answers as**): it reads only what that account can read. Changes it
  proposes (running work, changing settings) wait for approval in the web app, as in Chat; its answer says so and
  links to the conversation, through the Cloudflare tunnel while one is up (Settings › Remote access), so the link
  opens away from home too.
- **Approving in the room.** Reversible changes (merging, renaming or retyping entities, new namespaces) can be
  approved right there: the answer says "Reply “yes” to do it", and "yes" (or "no") does it (or declines it). With
  several waiting, "yes all" or "yes 12". Only the chat usernames listed may talk to Lens and approve, so it needs
  that list; **Approving in the room: only in the web app** turns it off. Settings, imports, batch runs, extensions
  and forgetting are always approved in the web app. Each approval is in the audit log with "via chat room" and who
  said yes.
- **Conversations.** Each person in each room is a conversation of that account's, titled
  "Matterbridge · room · person", listed in Chat with the account's other conversations. Follow-ups keep their context,
  and older messages are folded into a running summary ([Long conversations](assistant.md)).
- **A namespace's own assistant.** **Rooms for a namespace's assistant** gives a room to a namespace
  (`team = pods`, or one channel: `team/general = pods`). Conversations there are scoped to that namespace, so when its
  assistant is on ([A namespace's own assistant](assistant.md#a-namespaces-own-assistant)) it answers with its
  instructions and what it remembers, and a message starting with its name is for it too.
- **Routing in every other room.** In a room no namespace is given to, Lens is the way in to all of them and picks
  the namespace for each question: a message starting with a namespace assistant's name goes to that namespace (in
  any such room); otherwise the decision model picks the namespace the question is about, as the assistant home does
  ([Picking the namespace](assistant.md#picking-the-namespace-for-a-conversation-over-everything)). When it's sure, the
  answer looks there and says so once ("Looked in calls"); when it isn't, it looks everywhere and names the likeliest
  namespaces. **use pods** keeps the conversation in pods until **use everything**. The conversation's scope in Chat
  shows where the last question looked.
- **One reader.** Matterbridge hands out each message once, so only one server process reads it: the API and the
  workers all run the bridge thread, and whichever holds the lease (renewed every look, given up when it stops or the
  bridge is turned off) does the reading. Settings → Chat rooms says whether it is listening and how many messages it answered.

Settings (`bridge`): `enabled`, `url`, `token` (a secret), `account`, `name` (default Lens), `answer` (`mention` or
`all`), `gateway`, `users`, `approve` (`low_risk` or `off`), `rooms`, `poll_seconds` (default 2). The assistant can change them too ("connect Lens to
Matterbridge at …").

Refine later: voice notes and files posted in rooms, one Lens account per chat user.
