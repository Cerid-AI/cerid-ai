# Learning Techniques — Spaced Repetition, Active Recall

Decades of cognitive science research converges on a small set of learning techniques that consistently outperform alternatives. The techniques are unfashionably basic — flashcards beat highlighters, recalling beats rereading — but the evidence is overwhelming.

## Spaced repetition

Spaced repetition leverages the spacing effect: information reviewed at expanding intervals is retained dramatically longer than information reviewed in massed sessions. The classic finding (Ebbinghaus, 1885; replicated thousands of times) is that review timing matters more than total review time.

The mechanism: each successful retrieval slightly resets the forgetting curve. By spacing reviews to occur just before forgetting would have happened, you reinforce the memory at the moment of maximum benefit. Review too soon and the practice is wasted (you haven't forgotten anything yet); too late and the memory has decayed past easy retrieval.

**Software implementations** automate the scheduling:
- **Anki** — the most-used spaced-repetition tool. Free, open-source, ugly interface, devoted user base across language learners and medical students.
- **RemNote, SuperMemo, Mnemosyne** — alternatives with various trade-offs.
- **Quizlet** — popular but uses simpler scheduling; less effective for long-term retention.

The interval algorithm (typically a variant of SM-2) shows you a card; you rate how well you knew it; the algorithm schedules the next review accordingly. Cards you knew well show up less often; cards you struggled with show up more frequently.

Spaced repetition is most useful for:
- Vocabulary in a new language
- Anatomical and medical knowledge
- Programming language syntax and idioms
- Names, dates, specific facts that must be recalled precisely

It's less useful for:
- Conceptual understanding (better learned through worked problems)
- Procedural skills (better learned through practice)
- Material you only need to recognize, not produce

## Active recall

Active recall — generating an answer from memory — outperforms passive review (rereading, watching lectures, highlighting) by large margins. The "testing effect" is one of the most replicated findings in cognitive psychology.

The mechanism: the act of retrieval strengthens the memory and reveals what you don't actually know. Passive review feels productive because the material seems familiar, but familiarity is not knowledge — you can recognize what you can't produce.

Practical applications:

- **Practice problems before reading the chapter.** Failed attempts prime the brain to absorb the explanation when it comes.
- **Close the book and write what you know.** After reading, list everything you can recall before re-checking. The gaps are where to focus next.
- **Self-quiz at the end of each chapter or section.** Don't move on until you can summarize without looking.
- **Teach the material.** Explaining a concept exposes gaps faster than re-reading. The Feynman technique formalizes this — explain it simply, find what you can't explain, return to study.

The discomfort of trying to remember something you don't fully know is itself the learning. Most people quit too early because the discomfort feels like failure.

## Interleaving

Interleaving means mixing different topics or problem types within a study session rather than blocking. Studying topic A for an hour then topic B for an hour produces less retention than alternating — even though blocked study feels more productive in the moment.

The mechanism: interleaving forces the learner to identify which approach applies to each problem, which is the most transferable skill. Blocked study lets you cruise — every problem in this section uses the same technique, so you don't have to think about which technique to apply.

Practical applications:
- Mix problem types in math practice rather than doing all of one type then moving on
- Vary languages or topics in flashcard reviews
- For programming, alternate between debugging, refactoring, and feature implementation

The cost of interleaving is feel: it's harder, slower, and produces more errors per session. The retention benefit appears later.

## Elaboration

Elaboration means connecting new information to what you already know. Why does this idea fit (or not) with X? How is this analogous to Y? When have you seen this pattern before?

The mechanism: memories are retrieved through their connections. The more connections an idea has to existing knowledge, the more retrieval paths exist when you need it. Isolated facts have only one or two retrieval paths and get lost.

Elaboration combines well with active recall — instead of just answering "what is X?", explain X and how it relates to Y and Z. The longer answer requires more retrieval paths and produces denser learning.

## What doesn't work

Several beloved study techniques are less effective than commonly believed:

- **Highlighting and underlining** — feels active, mostly is passive. Marks the text as important but doesn't help you reproduce it.
- **Rereading** — produces familiarity, not knowledge. The test is whether you can produce the material, not recognize it.
- **Cramming** — works for short-term recall (an exam tomorrow), fails for retention beyond a week
- **Watching lecture videos at 2× speed without note-taking** — passive consumption disguised as productivity

These techniques aren't worthless — they're useful as supplements — but they don't substitute for active recall + spaced repetition.

## Putting it together

A high-leverage study workflow:

1. First read the material to get the shape of what's there
2. Generate flashcards or self-test questions for the key facts and concepts
3. Practice problems (or worked examples) to apply the concepts
4. Schedule spaced review of the flashcards in Anki or equivalent
5. Periodically interleave with related material to strengthen connections

The ratio of techniques varies by subject. Language learning: heavy on flashcards. Math: heavy on problem practice. Reading-comprehension subjects: heavy on summarization and connection-building.

The hardest part isn't picking the techniques. It's accepting that real learning feels hard in a way that the comforting illusions of passive study don't.
