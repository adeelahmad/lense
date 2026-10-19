# Playlists

Keep a moment of a recording, a video or a document as a clip, with a title and a note, and gather clips from any
resources into a playlist that plays them back to back.

## Keeping a clip

Select words in a transcript and choose **Add to playlist**, or choose the **Add to playlist** button beside a
highlight in its tab. One click: the clip goes at the end of the playlist you used last (a new one called Clips when
you have none), titled with the words you picked or the highlight's label, and the message that says where it went
has **Undo** and a link to the playlist. A clip of a selection runs from the first word to the last; a clip of a
single moment lasts 30 seconds, or to the end of the resource.

Anyone who can read a resource keeps clips of it, for themselves. That's how a clip differs from a
[highlight](api.md#highlights), which an editor makes for everyone who reads the resource. A clip of a highlight
copies its passage and label and stays when the highlight goes.

## Playing a playlist

**Playlists** in the menu (there once you have one; the toast links there before that) lists yours, the latest used first, then the ones others shared. Type a name and press Enter
to make one. A playlist's page plays its clips back to back: **Play all**, or the play button of any clip, then
Previous and Next. Each clip plays from its start to its end on its resource's own audio or video (the same signed link
the recording player uses) and then the next one starts; clips of text without audio are skipped, and **Open here**
opens the resource at the moment playing.

Drag a clip to reorder it, or use its Up and Down buttons; the order is saved at once. **Remove from playlist** takes a
clip out with Undo, which puts it back in its place. Rename a playlist by editing its name, share it with the
**Shared** switch, and delete it with Undo, which makes it again with its clips.

## Who sees what

A playlist is yours and private until you share it. A shared playlist is read by everyone signed in, and each person
gets only the clips of resources they have a role on, whoever made the playlist: a clip of a resource they can't read
isn't shown, nor are its title and note. Only you change your playlists. Clips go when their resource is deleted;
your private playlists go when your account is removed, and shared ones stay.

## Agents

Agents use playlists through the [API](api.md#playlists) and [MCP](mcp.md): `list_playlists` lists them (and one's
clips, with links to their moments), and `add_to_playlist` keeps a moment or a highlight as a clip, in a playlist
named by id or name, or by default the one used last.

## Refine later

- Play clips of text (documents, transcripts without audio) by reading them aloud instead of skipping them.
- Share a playlist with chosen people or a namespace, not only everyone signed in, and give it a public link.
- Export a playlist as one audio or video file, or as a podcast feed.
- Let the assistant in chat list playlists and add clips, as MCP does.
