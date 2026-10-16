# Feature 11: Read-only validation of AcroForm field values

Implement this feature completely in **Aspose-PDF-FOSS-for-Python**. Inspect the
current checkout, implement the API, add regression tests and documentation, run
the checks below, and review the final diff. Do not stop after proposing a plan.

The task is to validate values already stored in a PDF form. Callers should be
able to detect an incomplete application, an overlong answer, or a selection no
longer offered by a template before delivering the filled document.

**Do not commit, stage, push, or create a pull request.** Preserve unrelated work.
At completion, provide an English commit message explicitly calling this a new
feature and explaining its usefulness. Do not include assistant or tool
attribution in that message.

## 1. Repository context and constraints

- Read the applicable `AGENTS.md` before editing. All code comments and
  docstrings must be in English.
- Python 3.11+; source package is `src/aspose_pdf`, imported as `aspose_pdf`.
- Reuse `.venv`; use its Python for development and verification.
- Add no mandatory dependency, network call, service, credential, or environment
  file requirement.
- Keep changes scoped to form-value validation. Do not implement features 12–20.
- Do not manually edit generated build artifacts, caches, or bytecode.
- Implement a deterministic local check, not JavaScript execution or a general
  PDF, PDF/A, PDF/UA, signature, or XFA validator.

Read these existing implementations first; line numbers may change:

| File | Relevant behavior |
| --- | --- |
| `src/aspose_pdf/forms.py` | `Field`, inherited properties, `Form._load_fields`, `Form.to_dict`, `Form.fill`, `Form.reset`, retained-field lifecycle |
| `src/aspose_pdf/engine/form_fields.py` | `field_attribute`, `form_value`, `on_states`, `validate_choice_value` |
| `src/aspose_pdf/engine/form_field_edit.py` | Existing validation of options, flags, and field configuration |
| `src/aspose_pdf/engine/simple_pdf.py` | `get_form_fields`, `CosExtractor.extract_form_fields`, `_collect_fields_rec`, `_iter_form_fields` |
| `src/aspose_pdf/load_limits.py` | `PdfLoadLimits`, `_LoadBudget` |
| `src/aspose_pdf/__init__.py` | Canonical public imports and `__all__` |
| `tests/test_form_json.py` | Public authoring, fill, typed snapshots, round trips, limits |
| `tests/test_form_field_properties.py` | Inheritance, foreign-producer structures, widget/on-state fixtures |
| `tests/test_form_field_lifecycle.py` | Retained `Form` and `Field` handles after mutations |
| `supported-features.md`, `README.md` | Existing form capability claims and public examples |

Important current behavior to preserve:

1. `Field.required` is metadata; it does not currently cause a validation pass.
2. `max_length` is a viewer input limit. Programmatic filling may store a longer
   value. This feature must report it without changing filling behavior.
3. `Form.fill` validates supplied data, but cannot by itself diagnose all invalid
   values already present in a foreign PDF.
4. `form_value` normalizes values: checkbox state names become booleans, and a
   one-element choice array can become a scalar. It also tolerates some malformed
   values. A validator must not lose evidence of invalid types, duplicate
   selections, or undeclared checkbox states through that normalization.
5. Signature field values in the ordinary form snapshot are deliberately `None`,
   even for signed fields. Signature verification is a separate API.
6. `_iter_form_fields` currently has a hard 4096-visit cutoff and includes
   nonterminal nodes/widgets. Do not use that as an authoritative, silently
   truncated list of terminal fields for the new validator.

## 2. Required public API

Add this method to the canonical `Form` class:

```python
def validate(
    self,
    fields: Iterable[str] | None = None,
    *,
    strict: bool = True,
) -> list[FormValidationIssue]:
    ...
```

Add an immutable issue record, preferably a frozen dataclass with slots:

```python
@dataclass(frozen=True, slots=True)
class FormValidationIssue:
    field_name: str
    field_type: str
    code: str
    message: str
```

Expose the **same class object** through both imports:

```python
from aspose_pdf import FormValidationIssue
from aspose_pdf.forms import FormValidationIssue
```

Update the package's `__all__`. Do not invent a second generated implementation
or unrelated compatibility import path. No additional result wrapper, exception
mode, CLI, plugin, or `Field.validate()` method is required.

Return a fresh list of issues. An empty list means that the selected supported
fields have no problems covered by this API. Do not return a success boolean.

### Field selection and ordering

- `fields=None`: validate all current terminal data fields, in form order.
- An iterable selects exact, fully qualified field names in caller order.
- Accept lists, tuples, and generators. Validate each distinct name once;
  duplicates retain their first position.
- An empty iterable selects nothing and returns `[]`.
- Reject a bare string, bytes, bytearray, mapping, non-iterable, or a non-string
  element with `TypeError`. Do not coerce elements with `str()`.
- Unknown names raise `KeyError` when `strict=True`; skip them when
  `strict=False`. `strict` must be a real `bool`, not `0`, `1`, or another truthy
  value; otherwise raise `TypeError`.
- Check the complete requested selection before evaluating selected values. A
  generator that later fails or names an unknown field must not produce a
  partial returned report.
- Known signature fields and push buttons are skipped, even when explicitly
  selected. Nonterminal namespace parents and unnamed widget children are not
  separate data fields.
- Produce at most **one issue per selected field** using the precedence defined
  below. Keep field order stable; do not sort by message or issue code.
- Check document disposal first, including calls on a retained `Form` handle.
  Use the existing disposed-document exception behavior.

### Issue codes

Use exactly these machine-readable codes:

| Code | Meaning |
| --- | --- |
| `invalid_value` | The stored value has an unsupported type/shape, or contains duplicate selections |
| `required` | A required data field has no meaningful selection/value under the rules below |
| `max_length` | A text value exceeds a valid positive effective `/MaxLen` |
| `invalid_choice` | A present selection is not one of the permitted export/on-state values |

Precedence is `invalid_value`, then `required`, then `max_length`, then
`invalid_choice`. Stop evaluating a field once its first applicable issue is
known. Continue with other selected fields.

`field_type` uses the existing lowercase names: `text`, `checkbox`, `radio`,
`listbox`, or `combobox`. Messages must be nonempty, clear English explanations.
Tests should primarily assert names, types, and codes, rather than exact prose.
Do not embed current/default values or complete option lists in messages: the
report needs to remain safe to log for password and other sensitive fields.

## 3. Exact validation semantics

### 3.1 Shared rules

- Validate the **effective current `/V`**, including inherited `/V`, `/FT`, `/Ff`,
  `/MaxLen`, and `/Opt`. A local value, including explicit `PdfNull`, overrides
  the inherited value.
- An absent/null `/V` is empty. Do not substitute `/DV`: a default is what reset
  restores, not evidence that the current answer is filled.
- Include read-only and no-export data fields. This is a document-data check,
  not a browser form-submission implementation.
- Resolve indirect references using the existing engine and load budget.
- Use existing PDF string decoding and field-state conventions. Do not stringify
  a number, boolean, array, dictionary, or stream to make it look like valid text.
- For text and choice scalars, tolerate `PdfString` and `PdfName`, as the existing
  reader does. Unexpected COS types produce `invalid_value`, even if the ordinary
  field getter would return a string representation.
- This task validates current answers, not malformed field definitions. Missing
  or unusable `/MaxLen` means no usable length limit. Do not add a general
  metadata/appearance conformance audit.

### 3.2 Text fields

- Accept an effective scalar text value or absence/null. An array is invalid,
  including an array of one string.
- A required value is empty when absent/null, `""`, or whitespace-only according
  to Python `str.strip()`. Use stripping only to decide emptiness; never change
  the stored or reported data.
- Optional empty/whitespace-only values do not produce `required` issues.
- Enforce `/MaxLen` only when it is a finite positive whole number. Missing,
  nonnumeric, non-finite, fractional, zero, or negative limits are unusable and
  ignored by this value validator. Do not repair them.
- Count the decoded string with Python `len()`: Unicode code points, not encoded
  bytes or grapheme clusters. Document this policy explicitly. Count whitespace
  and hard line breaks for the length check; do not normalize Unicode/newlines.
- Exactly at the limit is valid; above it produces `max_length`.
- Apply the same rules to multiline, password, and comb text fields. Do not
  validate rendered clipping, glyph coverage, or comb flag combinations here.

### 3.3 Checkbox fields

Inspect the effective raw state before it becomes a boolean:

- Missing/null is unchecked.
- A `PdfName` with `Off` or an empty name is unchecked. Any other name is an on
  state; names such as `/No`, `/0`, and `/false` must not be mistaken for false.
- Tolerate legacy `PdfString` states according to the reader's current off-value
  convention: `""`, `"Off"`, `"0"`, `"false"`, and `"No"` are unchecked;
  other strings name on states. Preserve this distinction between names and
  strings without changing the existing getter.
- An array, number, boolean, dictionary, or stream is `invalid_value`.
- Required + unchecked produces `required`.
- For a selected on state, check membership in the declared on states returned
  by the existing `on_states` logic. When declared states exist and the current
  state is absent from them, return `invalid_choice`.
- If no on-state declarations can be read, check emptiness/type only. Do not
  invent a state such as `Yes`, synthesize appearances, or pretend to validate
  appearance completeness. Document this limitation.

### 3.4 Radio groups

- Accept a scalar `PdfName`/`PdfString`, or absence/null. Other types, including
  arrays, are `invalid_value`.
- Missing/null, `""`, and `"Off"` mean no selection. Do not apply the checkbox's
  other legacy off aliases to radio state names.
- A required unselected group produces `required`.
- When readable on states exist, a nonempty selection must match one exactly;
  otherwise return `invalid_choice`. Compare on-state/export names, not display
  labels or a radio group's unrelated `/Opt` text.
- As with checkboxes, absent readable on-state declarations limit the check to
  type and emptiness. Skip appearance integrity checks.
- Report once for the logical field, not once per widget, even across pages.

### 3.5 Fixed single-choice list/combo boxes

- Read allowed **export values**, not display labels, from the effective `/Opt`.
  A single-string option is both its export and display value.
- Accept a scalar text/name, an empty array as no selection, or a one-element
  array of text/name as one selection. A larger array is `invalid_value` even
  when every item is a valid option.
- Reject unsupported array item types instead of dropping them during decoding.
- Missing/null or an empty array is no selection: produce `required` only when
  the field is required.
- A present scalar must match an export value exactly; otherwise produce
  `invalid_choice`. Do not strip, case-fold, or accept the display label in place
  of its export value.
- An explicitly listed empty-string export is a valid selection, including for
  a required field. An empty string not listed as an export is `invalid_choice`.
- With no usable options, a present fixed selection is `invalid_choice`; an
  absent optional selection remains valid.

### 3.6 Editable combo boxes

- Use the same accepted scalar/array shapes as a single-choice field.
- A declared export value is valid, including an explicitly declared empty
  export. Other text is allowed as a custom answer.
- For a custom answer, required + empty/whitespace-only produces `required`.
  Optional custom text, including blank text, is valid.
- Do not apply a text field's `/MaxLen` to a choice field or manufacture an
  `invalid_choice` issue for legitimate custom text.

### 3.7 Multiselect list boxes

- Accept missing/null, a scalar text/name for a single selected item, or an array
  of text/name values. Single-scalar storage is already tolerated by the reader.
- Preserve array entries until type, duplicate, and cardinality checks have run.
  Do not lose a one-element array or ignore a nontext entry.
- A nontext item or repeated decoded selection produces `invalid_value`.
- No selected items produces `required` only when the field is required.
- Every selected item must exactly match a declared export value. Otherwise
  report one `invalid_choice` issue for that field, regardless of how many items
  are unknown. An empty-string export is selected when it is explicitly listed.

### 3.8 Explicit boundaries

Skip signature fields and push buttons, including when they carry the Required
flag. Do not use the ordinary form snapshot to infer whether a signature exists.

Do not implement JavaScript `/AA` validate/format/calculate/keystroke execution,
XFA validation, appearance-state synchronization, `/I` consistency checks,
custom validators, email/date/number business rules, schema configuration,
automatic corrections, or validation on every `save()`/`fill()` call. Existing
fill, reset, import/export, rendering, flattening, and signing behavior must stay
compatible.

## 4. Implementation approach and safeguards

Keep `Form.validate` as orchestration. Put substantial PDF/COS-specific logic in
a focused engine helper, for example `engine/form_validation.py`, if that keeps
the public module clear. The internal filename/layout may differ; the public
contract and behavior above should not.

Suggested sequence:

1. Add the issue record and its public exports.
2. Add focused public-API tests for required fields, length, and choices.
3. Build one bounded view of current terminal fields and their effective
   metadata/raw values; validate the requested names against it.
4. Evaluate independent predicates per field and construct immutable issues.
5. Add malformed-input, inheritance, lifecycle, laziness, and limit regressions.
6. Update documentation and run the release gate.

Reuse inherited-attribute and option/state decoding helpers where appropriate.
Reuse existing choice membership rules when their semantics match this spec, but
do not blindly pass normalized `Field.value` through a filling validator. In
particular, do not implement validation by assigning a value and catching the
setter's failure.

Do not re-traverse the complete field tree for every field. Avoid invoking
`field_dictionary(name)` independently for thousands of names if it rescans the
whole tree. A shared, bounded field/metadata index or carefully scoped helper is
preferable. Do not bypass or silently truncate valid forms beyond 4096 nodes.

Refreshing Python-side field caches is allowed when needed for current values,
but retain existing `Field` object identity/lifecycle behavior. A retained `Form`
must see additions, removals, imports, and value/configuration changes. Returned
issues are snapshots and must not change with later form edits.

### Read-only behavior

The validator must not change COS dictionaries, `/V`, `/DV`, `/Ff`, `/Opt`, `/I`,
`/AS`, `/AP`, `/NeedAppearances`, page content, or the set of registered objects.
Do not set dirty/save flags or regenerate appearances. It must not require a
subsequent save or cause an otherwise unchanged incremental save to append a
revision. It must neither open output files nor overwrite the source document.
Loading an existing object into a lazy cache is allowed; it is not the same as
allocating/registering a new PDF object. Non-mutation assertions must distinguish
those two operations.

### Lazy I/O and resource limits

- Validation requires form metadata, not page drawing streams. Do not call
  `get_page_content`, render pages, scan all widget page locations for the issue
  record, or decode appearance/font/image/attachment streams.
- Use the document's configured `PdfLoadLimits` for field traversal, requested
  names, inheritance depth, option/value arrays, tracking sets/maps, and issue
  accumulation. Check before unbounded materialization/appends; do not do
  `list(fields)` on an unchecked generator.
- Bound cyclic/repeated field graphs and parent chains. Do not hang, hit Python
  recursion limits, silently skip the rest of a valid large form, or return a
  clean report after a traversal failed.
- Ordinary field-value problems return issues. Argument errors, disposal,
  structural parse failures, I/O failures, and resource-limit failures raise the
  existing appropriate exceptions; they are not successful validation results.
- **`PdfResourceLimitException` subclasses `PdfValidationException`.** If you
  catch the latter when adapting an existing helper, re-raise resource-limit
  exceptions first. Never turn a budget failure into `invalid_choice` or `[]`.

## 5. Required regression tests

Add `tests/test_form_validation.py`; split it into additional focused files only
if needed. Prefer public APIs to construct normal fixtures and make assertions.
For foreign/malformed PDFs and inheritance, small COS fixtures are appropriate;
use the patterns in the existing form tests. Do not mock the predicates being
tested or merely assert that an internal helper was called.

### A. API, selection, and ordering

1. Empty document and a page without AcroForm return `[]`.
2. A valid mixed form returns `[]`; a mixed invalid form returns all applicable
   fields, with exact stable codes/types/order and one issue per field.
3. Canonical root/module imports point to the same immutable issue class; issues
   have a useful repr and cannot be edited.
4. Selected names use caller order; duplicates are checked once; generators and
   an empty selection work.
5. Unknown names raise by default and are skipped with `strict=False`.
6. Wrong outer selection types, non-string elements, and non-bool `strict`
   values raise `TypeError`. Include a generator with a bad later element/name.
7. A retained `Form` raises after document disposal, including empty selection.
8. Explicitly selected known signature/push-button fields are skipped, whereas
   unknown names still obey `strict`.

### B. Required values and inheritance

9. Required text: absent, null, empty, spaces/tabs/newlines, and Unicode
   whitespace fail; meaningful text passes. Optional equivalents pass.
10. Required checkbox: unchecked fails and checked passes; test custom on states
    named `No`, `0`, and `false` as **PdfName** objects. Test the distinct legacy
    PdfString off-alias rules separately.
11. Required radio: unselected fails, valid selection passes, and a group with
    several widgets produces only one issue.
12. Required single/multiple choices: absent/empty selection fails; valid choices
    pass. Explicit empty-string exports obey the rules in section 3.
13. Inherited Required, `/V`, `/MaxLen`, and `/Opt` work. Cover an unnamed parent
    and a named terminal child; a local override and a sibling remain independent.
14. An inherited `/V` counts as current; local `PdfNull` overrides it. `/DV`
    alone does not satisfy a required field.
15. Read-only/no-export fields are still checked; required signature/push-button
    fields are skipped.

### C. Text length

16. Below/exactly/above `/MaxLen`, inherited limits, and local overrides.
17. Unicode code-point counting, including an astral character and a combining
    sequence; a multiline hard break and leading/trailing spaces count.
18. Missing, zero, negative, fractional, nonnumeric, and non-finite limits are
    ignored without mutation. Craft in-memory non-finite metadata rather than
    expecting the PDF writer to serialize an invalid number.
19. Overlong programmatic filling remains possible; `validate()` reports it,
    leaves the full value intact, and does not alter the fill/setter contract.
20. Password and comb/multiline fields obey the same value rules; messages never
    contain the actual password/current/default value.

### D. Choices, state names, and malformed stored values

21. Export/display pairs: a valid export passes; its different display label is
    invalid. Matching is exact, including case and whitespace.
22. Fixed combo/list unknown values fail; editable combo custom values pass.
    Required blank custom text fails unless it is an explicitly declared export.
23. Single-choice scalar, empty array, and one-element array follow the contract;
    arrays containing several selections fail with `invalid_value`.
24. Multiselect scalar, zero/one/many array entries, duplicates, mixed-type array
    items, and unknown exports. Several unknown items produce one issue.
25. Undeclared checkbox/radio on states are reported when readable states exist.
    Test absent declarations separately: type/required checks still work without
    inventing an appearance-related error.
26. Numeric/boolean/dictionary/stream values and text-field arrays produce
    `invalid_value`, even when the ordinary reader would stringify/collapse
    them. Do not silently drop malformed entries from arrays.
27. Error precedence is deterministic, e.g. malformed required text is
    `invalid_value`, required blank overlong text is `required`, and duplicate
    selections containing an unknown export are `invalid_value`.

### E. Round trips, lifecycle, and non-mutation

28. Exercise authored and foreign/COS fixtures through in-memory PDF bytes and
    file save/reload paths where practical. Assert the public report after reload.
    Do not serialize intentionally unserializable test-only COS values.
29. Revalidate after `fill`, direct value/configuration edits, reset, add/remove,
    and an existing form-data import path. Retained handles see current values;
    previous issue objects remain unchanged.
30. Check a retained form after merge/flatten when appropriate, reusing existing
    lifecycle fixtures. Do not resurrect removed fields or change retained field
    identities just to validate.
31. Snapshot relevant COS serialization/object counts and public values before
    validation, then assert they are unchanged on successful reports and API
    failures. Use a spy/failing stub to prove no setters, appearance generation,
    or page-content edits occur.
32. For an already loaded, serializable PDF, an unchanged incremental save before
    versus after validation remains unchanged and adds no revision. Use an
    existing deterministic round-trip fixture; do not compare two separately
    authored documents with random IDs/timestamps.

### F. Streaming, malformed graphs, and budgets

33. With `Document.open_streaming`, fail the test if validation calls
    `get_page_content`, rendering, or stream decoding. Cover both valid and
    invalid fields; metadata reads are allowed.
34. Low `max_container_items` caps requested-name generators and issue/options/
    value accumulation. Low nesting limits bound field/inheritance traversal.
    Assert `PdfResourceLimitException`, not a partial/empty report.
35. Demonstrate that a resource-limit exception raised from a reused choice or
    decoding path propagates rather than becoming an ordinary issue. A focused
    exception-injection test is acceptable in addition to real small-budget tests.
36. Small cyclic/repeated field and parent graphs terminate through the defined
    parser/limit error path and never report successful validation.
37. Include a cheap well-formed form beyond the existing 4096-node convenience
    walker cutoff, under permitted limits, with an invalid final terminal field.
    It must be found. Build lightweight field dictionaries without thousands of
    appearance streams; no timing-based performance assertion is needed.

The numbering is a scenario checklist, not a demand for exactly 37 tests.
Parametrize related cases, keep fixtures deterministic, use `tmp_path`, and
dispose documents with context managers. Do not turn the suite into an unrelated
parser audit or silently weaken these cases to match the implementation.

## 6. Documentation and example

Update the Forms section of `supported-features.md` with the method, issue fields
and codes, field-selection ordering/strictness, type-specific empty-value policy,
Unicode length policy, read-only behavior, exception/limit behavior, and scope
boundaries. Explain the difference between setter validation and checking
already stored data. Document the limited state-membership check when button
on-state metadata is absent.

Add a concise public example and API entry to `README.md`, such as:

```python
from aspose_pdf import Document

with Document("application.pdf") as document:
    document.form.fill({"person.name": "Alice"})
    issues = document.form.validate()
    if issues:
        for issue in issues:
            print(issue.field_name, issue.code, issue.message)
    else:
        document.save("completed.pdf")
```

State that this method does not run embedded JavaScript, validate signatures, or
certify PDF conformance. Do not imply that every external template business rule
has been validated just because the list is empty.

## 7. Verification commands

Use the smallest relevant tests while developing, then finish the full release
gate on the final implementation. For example, from the repository root:

```bash
.venv/bin/python -m pytest tests/test_form_validation.py -q
.venv/bin/python -m pytest \
  tests/test_forms.py \
  tests/test_form_json.py \
  tests/test_form_reset.py \
  tests/test_form_field_properties.py \
  tests/test_form_field_lifecycle.py \
  tests/test_generated_reexports.py -q
.venv/bin/python -m ruff check src/
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src/aspose_pdf
git diff --check
```

Build and check the final package, preferably with output isolated from old
distributions. Use the repository's required build toolchain:

```bash
task_build_dir=$(mktemp -d)
.venv/bin/python -m build --outdir "$task_build_dir"
.venv/bin/python -m twine check "$task_build_dir"/*.whl "$task_build_dir"/*.tar.gz
```

Also smoke-test the built wheel in a clean temporary environment with only its
normal dependencies: import `FormValidationIssue` from the public package,
create/read a form, obtain a required-field issue, fill it, and obtain an empty
report. Verify that the import actually comes from the built wheel/environment,
not the editable source checkout. Confirm the issue class is included in the
distribution and that validation needs no optional image/layout packages.

Report only commands and outcomes you actually observed. If an existing failure
or environment problem blocks a gate, identify it precisely and distinguish it
from a passing check; do not invent a pass count. Fix failures caused by this
change. Review `git diff` and `git status` before handing off.

## 8. Definition of done and handoff

The feature is complete only when:

- The public method and immutable issue type match this contract.
- Required, length, choice, and malformed-value checks cover the listed types
  and return deterministic reports with the agreed precedence.
- Selection, inheritance, raw-value normalization pitfalls, lifecycle, and
  large/cyclic form handling are covered by meaningful tests.
- Validation is read-only and preserves lazy page/appearance streams.
- Resource/parser failures cannot be mistaken for a clean report.
- Existing form manipulation, rendering, signing, and import behavior remains
  compatible; no new automatic validation hook was introduced.
- Documentation accurately states tested behavior and its limits.
- Focused tests, the full suite, lint, compile, build, package metadata checks,
  and the wheel smoke test have been run and their actual results recorded.
- The diff is scoped; there is no commit, staging, push, or pull request.

In the final response provide:

1. A concise description of what changed and why it is useful.
2. A small API usage example, important limitations, and actual checks/results.
3. An English commit message with a title such as
   `Add new feature: validate stored AcroForm field values`, followed by a short
   explanation of how this catches incomplete or inconsistent forms before
   delivery. It may exceed four lines. Include no assistant/tool attribution.
