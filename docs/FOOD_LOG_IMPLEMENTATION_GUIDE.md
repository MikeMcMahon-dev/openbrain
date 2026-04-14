# Food Log Implementation Guide

## TL;DR
Your Custom GPT (mike.mcmahon67) is now configured to enforce a structured food log format. This makes your logs retrievable, queryable, and trend-able.

**Start using it immediately:**
1. Tell your Custom GPT: "Log today's meals"
2. The GPT will format entries with date, meal type, items, and macros
3. Entries are saved with subject `food-log` and topic `2026-04-14` (today's ISO date)
4. Retrieval now works: "What did I eat on 2026-04-14?" or "Show my breakfast pattern"

---

## What Changed

### Before (Unstructured)
```
Breakfast: eggs and toast
Lunch: chicken and rice
Estimated macros somewhere... unclear
```
→ **Problem:** Vector search can't distinguish meals. Queries return fragments.

### After (Structured)
```markdown
# Food Log: 2026-04-14

## Meal: BREAKFAST
**Time**: 07:30
**Items**: 2 eggs (scrambled), toast, butter
### Macros (estimated)
- Protein: 18g
- Carbs: 35g
- Fat: 18g
- Calories: 410 kcal

## Meal: LUNCH
...
```
→ **Solution:** Metadata is machine-readable. Queries retrieve the exact day or meal pattern.

---

## Immediate Steps

### Step 1: Update Your Custom GPT System Prompt
1. Go to ChatGPT > "OpenBrain" (or create/edit your Custom GPT)
2. Click **"Configure"** (pencil icon)
3. Scroll to **"System Prompt"** section
4. Replace the prompt with the content in `/docs/gpt_instructions/mike_mcmahon67.md`
   - **Easy way**: Copy the section below (Updated System Prompt)
5. Save

### Step 2: Use It
Ask your Custom GPT any of these:
- "Log today's meals with macros"
- "I had eggs and toast for breakfast, grilled chicken for lunch..."
- "Save a food log entry for 2026-04-14"

The GPT will:
1. Format everything to spec
2. Ask you to confirm macros (or estimate them)
3. Save to OpenBrain with the correct metadata

### Step 3: Retrieve Past Logs
Now that entries are tagged with `subject: food-log`, you can:
- "Show my food logs for April"
- "What was my protein intake last week?"
- "Do I have energy crashes after carb-heavy lunches?"
- "Show my breakfast patterns"

---

## Updated System Prompt

Copy this entire block and paste it into your Custom GPT's System Prompt section:

```
You are OpenBrain, a personal knowledge and memory assistant connected to
a private vault of notes, homelab documentation, infrastructure reference
material, project docs, and food logs.

## When querying
1. Always call openbrain_query first. The vault is the primary source.
2. Follow the tutor_prompt and rules fields from the response exactly.
3. Check the query_confidence field in the response:
   - high: answer directly from the vault.
   - medium: answer from the vault, note at the end: "Confidence is
     moderate — verify against primary source if this is critical."
   - low: flag it before answering: "Low confidence result — my notes
     may not cover this well. Supplementing with web search." Then
     search and clearly separate what came from the vault vs the web.
4. Use web search only to fill gaps the vault does not cover. When you do,
   say clearly: "Your notes don't cover this part, but..."
5. Never silently mix vault content and web content.

## Flashcards
1. Call openbrain_generate_flashcards.
2. Present front / back format. One card at a time unless asked for all.

## Quizzes
1. Call openbrain_generate_quiz.
2. One question at a time. Wait for answer before revealing correctness.

## Saving notes
When asked to remember, save, or capture something:
1. Call openbrain_ingest with source_type "text".
2. Confirm in one sentence.

## Saving a URL
When asked to save, remember, or ingest a webpage or link:
1. Call openbrain_ingest with source_type "url" and source = the URL exactly as given.
2. The server fetches and extracts the page content — do not copy the text yourself.
3. Confirm in one sentence.

## Ingesting uploaded documents
When a file is uploaded to save:
1. Under 2000 words: call openbrain_ingest once, source_type "text".
2. Longer: split into ~1500 word sections at natural breaks, call
   openbrain_ingest once per section, same subject and topic, noting
   "part 1 of N" in the topic. Confirm total sections saved.

## Saving food logs
When asked to log meals or food entries:
1. **Format the entry** using the Food Log Format Spec exactly:
   - Header: `# Food Log: YYYY-MM-DD` (ISO date)
   - Each meal starts with `## Meal: [BREAKFAST|LUNCH|DINNER|SNACK]`
   - Include `**Time**: HH:MM`, `**Items**: comma-separated foods`
   - Always include `### Macros` section with Protein (g), Carbs (g), Fat (g), Calories (kcal)
   - Optional `### Notes` for hunger, energy, digestion observations
   - Separate multiple meals with `---` (horizontal rule)
2. Call openbrain_ingest with:
   - `source_type`: "text"
   - `subject`: "food-log" (exactly, lowercase)
   - `topic`: "YYYY-MM-DD" (ISO date of the log)
3. Confirm with date and meal count. Example: "Logged 4 meals for 2026-04-14."

**Example call for a day's food:**
```json
{
  "source_type": "text",
  "source": "# Food Log: 2026-04-14\n\n## Meal: BREAKFAST\n**Time**: 07:30\n**Items**: eggs, toast, butter\n\n### Macros (estimated)\n- Protein: 18g\n- Carbs: 35g\n- Fat: 18g\n- Calories: 410 kcal\n\n---\n\n## Meal: LUNCH\n...",
  "subject": "food-log",
  "topic": "2026-04-14"
}
```

**Retrieval tips:**
- Query for specific dates: "food log 2026-04-14"
- Query for patterns: "food log breakfast macros" or "food log energy levels"
- Query for ranges: "food log April 7-14"

## Tone
Technical, but appreciates humor. Lead with the answer.
```

---

## Format Checklist

When the GPT logs a meal, it should always include:

- [ ] **Date header**: `# Food Log: YYYY-MM-DD` (top of entry)
- [ ] **Meal type header**: `## Meal: BREAKFAST` (or LUNCH, DINNER, SNACK)
- [ ] **Time**: `**Time**: HH:MM` (or estimated if needed)
- [ ] **Items**: `**Items**: food1, food2, food3` (comma-separated, descriptive)
- [ ] **Protein**: `- Protein: XXg` (in grams, lowercase `g`)
- [ ] **Carbs**: `- Carbs: XXg` (in grams)
- [ ] **Fat**: `- Fat: XXg` (in grams)
- [ ] **Calories**: `- Calories: XXXX kcal` (lowercase `kcal`)
- [ ] **Meals separated**: `---` (horizontal rule between meals)
- [ ] **Metadata**: subject=`food-log`, topic=`YYYY-MM-DD`

If a meal is missing any of these (except time and notes), the GPT should ask for clarification before saving.

---

## Macro Estimation Tips

If you don't know exact macros, the GPT can estimate:

| Food | Protein | Carbs | Fat | Calories |
|------|---------|-------|-----|----------|
| 1 egg | 6g | 0.5g | 5g | 70 kcal |
| 2 slices toast (wheat) | 6g | 40g | 2g | 160 kcal |
| 1 tbsp butter | 0g | 0g | 12g | 100 kcal |
| 6 oz grilled chicken | 42g | 0g | 6g | 240 kcal |
| 1 cup cooked rice | 4g | 45g | 1g | 190 kcal |
| 1 cup broccoli | 4g | 8g | 0.5g | 35 kcal |

Or just tell the GPT: "I had [foods]. Can you estimate the macros?" and it will look them up.

---

## Example Conversation: With Macro Lookup

**You**: "Log breakfast. I had 2 eggs, toast with butter, and coffee."

**Custom GPT**: 
> I'll look up the nutrition info for those items.
> 
> ✓ 2 large eggs (scrambled): Protein 12g | Carbs 1g | Fat 10g | Calories 140 kcal
> ✓ 2 slices whole wheat bread: Protein 6g | Carbs 40g | Fat 2g | Calories 160 kcal  
> ✓ 1 tbsp butter: Protein 0g | Carbs 0g | Fat 12g | Calories 100 kcal
> ✓ Black coffee: negligible macros
>
> **BREAKFAST TOTAL: Protein 18g | Carbs 41g | Fat 24g | Calories 400 kcal**
>
> What time was breakfast? And any notes on how you felt?

**You**: "7:30am, felt energized"

**Custom GPT**: 
> Logged breakfast for 2026-04-14.

---

## Example Conversation: When User Provides Some Macros

**You**: "Log lunch — grilled chicken, brown rice, broccoli. I think the chicken was about 200g and rice was a cup. Not sure about macros though."

**Custom GPT**: 
> Looking up nutrition for those quantities:
>
> ✓ Grilled chicken breast 200g (7 oz): Protein 42g | Carbs 0g | Fat 8g | Calories 240 kcal
> ✓ Cooked brown rice 1 cup: Protein 5g | Carbs 45g | Fat 2g | Calories 215 kcal
> ✓ Roasted broccoli 2 cups: Protein 6g | Carbs 12g | Fat 0.5g | Calories 70 kcal
>
> **LUNCH TOTAL: Protein 53g | Carbs 57g | Fat 10.5g | Calories 525 kcal**
>
> What time was lunch? Do these look about right, or do you want me to adjust any quantities?

**You**: "Noon, looks good"

**Custom GPT**: 
> Logged lunch for 2026-04-14.

---

## Example Conversation: Multi-Meal Day

**You**: "Log all my meals for today. Breakfast was eggs and toast at 7:30, lunch was chicken and rice at 12:30, snack was apple and almonds at 4, dinner was salmon and pasta at 7."

**Custom GPT**: 
> I'll look up nutrition for all of that. Hold on...
>
> **BREAKFAST (7:30am)**
> - 2 eggs scrambled, 2 toast, 1 tbsp butter
> - Protein 18g | Carbs 41g | Fat 24g | Calories 400 kcal
>
> **LUNCH (12:30pm)**
> - 6 oz grilled chicken, 1 cup brown rice, 2 cups broccoli
> - Protein 53g | Carbs 57g | Fat 10.5g | Calories 525 kcal
>
> **SNACK (4:00pm)**
> - 1 medium apple, 1 oz almonds
> - Protein 6g | Carbs 30g | Fat 9g | Calories 195 kcal
>
> **DINNER (7:00pm)**
> - 6 oz salmon, 1 cup pasta, 1 tbsp olive oil
> - Protein 40g | Carbs 50g | Fat 23g | Calories 530 kcal
>
> **DAILY TOTAL: Protein 117g | Carbs 178g | Fat 66.5g | Calories 1,650 kcal**
>
> Any adjustments to these estimates, or any notes about energy/digestion I should add?

**You**: "Good, but I had 2 tbsp olive oil, not 1"

**Custom GPT**: 
> Adjusting dinner: 1 tbsp → 2 tbsp olive oil adds 12g fat and 100 kcal.
>
> **DINNER REVISED: Protein 40g | Carbs 50g | Fat 35g | Calories 630 kcal**
> **DAILY TOTAL REVISED: Protein 117g | Carbs 178g | Fat 78.5g | Calories 1,750 kcal**
>
> Logged 4 meals for 2026-04-14.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "I can't find my food logs" | Make sure you're querying with the right subject. Try: "Show food logs from 2026-04-14" |
| "Logs are coming back fragmented" | Old unstructured logs may still be in the vault. New queries will prefer the well-formatted ones. You can re-log old dates if needed. |
| "The GPT isn't formatting consistently" | Make sure the system prompt is updated (Step 1 above). The GPT needs the explicit food log rules. |
| "I want to log multiple days at once" | Log each day separately (call openbrain_ingest once per date). This keeps each day independently queryable. |
| "Macros are a rough estimate—is that OK?" | Yes! Consistency matters more than precision. Log what you can estimate, and patterns will still be visible in queries. |

---

## Next Steps

1. **Update your Custom GPT** with the system prompt above (Step 1)
2. **Test it**: Ask your GPT to log today's meals
3. **Verify**: Query OpenBrain: "Show me today's food log"
4. **Refine**: If entries need tweaking, tell the GPT and it will adjust
5. **Track trends**: After a few days of structured logs, you can ask things like:
   - "Do I have more energy on high-protein days?"
   - "What's my average breakfast calorie intake?"
   - "Show my meal timing pattern over the past week"

---

## Reference
- **Full spec**: `FOOD_LOG_FORMAT_SPEC.md`
- **Custom GPT instructions**: `docs/gpt_instructions/mike_mcmahon67.md`
- **SafeIngest rules**: Will not flag food logs (no injection keywords)
