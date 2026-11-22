# Components

The folder structure follows the spec's §6.2 tree, file for file. Five extra
components live inside their feature folder because they are parts of a named
component rather than new surfaces — they are listed under their parent below so
the mapping stays unambiguous.

## The spec tree

```
layout/     AppSidebar  Topbar  WorkspaceSwitcher
dashboard/  WelcomeCard  RecentNotes  KnowledgeStats
notes/      NoteEditor  NoteToolbar  TagSelector  BacklinksPanel
documents/  FileUploader  DocumentCard  DocumentPreview  ProcessingStatus
assistant/  ChatWindow  MessageBubble  SourceCitation  ChatInput
graph/      KnowledgeGraph  GraphControls  NodeDetails
```

## Extras, and what they belong to

| File | Part of | Why it is separate |
|---|---|---|
| `notes/MarkdownView.svelte` | `NoteEditor` | The preview half. Also used by the read-only note view, so it cannot live inside the editor's local state. |
| `assistant/AnswerBody.svelte` | `MessageBubble` | Inline citation chips inside streamed text: a different concern from the bubble's layout. |
| `assistant/CitationChip.svelte` | `AnswerBody` | One marker, used both inline and in the sources list. |
| `dashboard/QuickCapture.svelte` | `WelcomeCard`'s capture panel | Reuses `documents/FileUploader`, so it is a composition rather than a variant of the welcome card. |
| `dashboard/KnowledgeGraphMini.svelte` | `WelcomeCard`'s graph panel | The preview shares data shape with `graph/KnowledgeGraph` but not layout: fixed radial, no interaction. |

## Shared, at the root

`EmptyState` and `ErrorNotice` are cross-feature and carry no domain knowledge.
`WorkspaceMembers` belongs to Settings, which the §6.2 tree does not cover.

## Two rules this structure enforces

**Pages compose; components do not fetch.** A page owns data loading, the active
workspace, and anything that must outlive a child re-render (the assistant's
conversation id, the note's autosaver). Components take props and emit callbacks.

**One upload path.** `FileUploader` implements `upload-url → Storage PUT →
confirm` once. The Documents page and QuickCapture both render it; a second
copy would eventually drift on which MIME types it accepted.
