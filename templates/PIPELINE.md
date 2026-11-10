# Conversation Pipeline (generic template)

The room flow the kit implements. Your production's version of this
document is the governing description; the kit's prompts restate it.

## Roles
- Guest agent: plays one character, holding only that character's own
  dossier plus the committed public transcript.
- Host agent: plays the host, who mediates and asks questions.
- Watcher: audits each proposed turn (deletion, structure,
  contingency, read-aloud, tags, facts). One objection maximum.
- Mediator (the kit): deterministic rules - who speaks, what commits.
- Auditor: reads the finished room and flags defects; never rewrites.

## Per-turn loop
1. The kit calls the floor for one speaker.
2. The speaker's fresh agent proposes a turn.
3. The watcher passes it, or objects once (strongest objection only).
4. On an objection, the speaker restates the turn once.
5. A re-check is recorded for the record; it does not veto.
6. The turn commits either way. The room moves on.

## Standards in force
- Turn budgets: median guest turn roughly fifteen to twenty-five
  words; at least a third of turns ten words or fewer; a guest turn
  over fifty words needs a stated reason. Short reactions are legal.
- Floor offers are name-only ("Alex?"), never a formula.
- Booked friction: the run-of-show names one real disagreement; the
  host must actually press it, and the record says whether it happened.
- Facts only from the shared brief (occasion), the conversation, or a
  speaker's own field, per the dossier.
