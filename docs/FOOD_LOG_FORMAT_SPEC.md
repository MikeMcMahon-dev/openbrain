# Food Log Entry Format Specification

## Purpose
Standardize food log entries for reliable retrieval, trending, and macro analysis in OpenBrain.

---

## Entry Format (Markdown Structured Text)

Each food log entry MUST follow this exact format:

```
# Food Log: YYYY-MM-DD

## Meal: [BREAKFAST|LUNCH|DINNER|SNACK]
**Time**: HH:MM (local time, optional)
**Items**: [comma-separated list of foods]

### Macros (estimated)
- Protein: XXg
- Carbs: XXg
- Fat: XXg
- Calories: XXXX kcal

### Notes (optional)
[Any additional context: hunger level, energy, digestion notes, etc.]

---
```

## Example Entry

```
# Food Log: 2026-04-14

## Meal: BREAKFAST
**Time**: 07:30
**Items**: 2 eggs (scrambled), 2 slices whole wheat toast, 1 tbsp butter, black coffee

### Macros (estimated)
- Protein: 18g
- Carbs: 35g
- Fat: 18g
- Calories: 410 kcal

### Notes (optional)
Felt good after eating; energy level 8/10.

---

## Meal: LUNCH
**Time**: 12:15
**Items**: grilled chicken breast (6 oz), 1 cup brown rice, roasted broccoli (2 cups)

### Macros (estimated)
- Protein: 42g
- Carbs: 55g
- Fat: 6g
- Calories: 450 kcal

### Notes (optional)
Good satiety; no afternoon slump.

---

## Meal: SNACK
**Time**: 16:00
**Items**: apple, 1 oz almonds

### Macros (estimated)
- Protein: 6g
- Carbs: 30g
- Fat: 9g
- Calories: 195 kcal

---

## Meal: DINNER
**Time**: 18:45
**Items**: salmon fillet (6 oz), 1 cup pasta, olive oil (1 tbsp), green salad with vinaigrette

### Macros (estimated)
- Protein: 38g
- Carbs: 45g
- Fat: 22g
- Calories: 545 kcal

### Notes (optional)
Sleep quality prediction: 8/10 (light dinner, good timing).
```

---

## Ingest Metadata (How to Save)

When saving a food log entry to OpenBrain via Custom GPT:

| Field | Value | Example |
|-------|-------|---------|
| **subject** | `food-log` (fixed) | `food-log` |
| **topic** | `YYYY-MM-DD` (date of log) | `2026-04-14` |
| **source_type** | `text` | `text` |

**Full example ingest call:**
```json
{
  "source_type": "text",
  "source": "[the food log markdown from above]",
  "subject": "food-log",
  "topic": "2026-04-14"
}
```

---

## Retrieval Tags

When retrieving past entries, use these query patterns:

| Use Case | Query | Returns |
|----------|-------|---------|
| Specific date | `food log 2026-04-14` | All meals from that day |
| Weekly summary | `food log April 7-13` | Range of entries |
| Macro trends | `food log protein Tuesday` | Protein intake on that weekday |
| Meal type | `food log breakfast patterns` | All breakfasts from recent logs |
| Energy correlation | `food log lunch energy` | Lunches with energy notes |
| Digestion notes | `food log digestion issues` | Meals with digestion context |

---

## Validation Checklist

Before saving, ensure:
- [ ] Date header is ISO format: `YYYY-MM-DD`
- [ ] Each meal has `**Time**` (even if estimated, optional field OK to skip)
- [ ] Each meal has `**Items**` as comma-separated list
- [ ] Macro fields use `XXg` or `XXXX kcal` (lowercase `g` and `kcal`)
- [ ] All three macros (protein, carbs, fat) are present
- [ ] Calories are always included
- [ ] Meal type is one of: `BREAKFAST`, `LUNCH`, `DINNER`, `SNACK`
- [ ] Entries are separated by `---` (horizontal rule)
- [ ] Subject is exactly `food-log` (lowercase, hyphenated)
- [ ] Topic is the date: `YYYY-MM-DD`

---

## Why This Format Works

### For Retrieval (Vector Search)
- **Date in topic**: Exact date matching in metadata
- **Consistent structure**: Embeddings are more predictable
- **Macro labels standardized**: "Protein: 42g" vs "42g protein" won't fragment results

### For Trending
- **ISO date format**: Easy to parse and sort
- **Meal type labels**: Can aggregate by meal type across days
- **Macro consistency**: Can sum and calculate averages

### For Analysis
- **Notes field**: Contextual markers (energy, digestion, satiety) are searchable
- **Macro format**: Can be regex-extracted for spreadsheet export if needed
- **Subject tag `food-log`**: Filters out other OpenBrain content in queries

---

## Multi-Day Entries

If logging multiple days in one ingest (e.g., a Sunday log for Mon-Fri):

**Option 1: Single ingest, topic = last date**
```json
{
  "source_type": "text",
  "source": "[Monday] ... [Tuesday] ... [Wednesday] ...",
  "subject": "food-log",
  "topic": "2026-04-11_multi_day_log"
}
```

**Option 2: Separate ingests (Recommended)**
Call `openbrain_ingest` 5 times, one per date:
```json
{"source_type": "text", "source": "[Monday log]", "subject": "food-log", "topic": "2026-04-07"}
{"source_type": "text", "source": "[Tuesday log]", "subject": "food-log", "topic": "2026-04-08"}
...
```

**Option 2 is better** because each entry is independently queryable and datable.

---

## Migration from Unstructured Logs

If you have existing unstructured food notes:

1. Query OpenBrain: `"food logs I've saved"`
2. For each entry found:
   - Reformat to match this spec
   - Save with new structured format (same date, fresh ingest)
   - Old unstructured entry will remain, but new queries will hit the structured one
3. Over time, queries naturally prefer the well-formatted structured entries

---

## Future Enhancements

Possible improvements (not required now):
- [ ] Add `workout` field for exercise context
- [ ] Add `sleep_quality` field for post-meal recovery tracking
- [ ] Add `hunger_before` / `satiety_after` numeric scale
- [ ] Weekly summary aggregation
- [ ] Integrate with Apple Health / Cronometer export
