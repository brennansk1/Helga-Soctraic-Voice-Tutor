"""What the pair miner accepts, and — more importantly — what it refuses.

A pair goes into the tutor's prompt as an IMPERATIVE ("show this error, ask
where they would look"). So a bad pair is not a missed opportunity, it is a
wasted turn built on material that does not teach anything. The miner returning
nothing is always better than the miner returning a guess, because once a pair
is in the prompt the tutor cannot tell the difference.

These tests therefore weight the negative cases as heavily as the positive ones.
"""
from services.domains.computer_science import code_pairs as cp

ERROR_THEN_FIX = """
```text
Compilation Error in model my_model
  dbt0101: no viable alternative at input '(    )'
```
The fix:
```sql
select * from {{ ref('upstream') }}
```
"""

CMD_THEN_OUTPUT = """
```bash
dbt run --select my_model
```
```text
14:03:22  Finished running 1 table model
Completed successfully
Done. PASS=1 WARN=0 ERROR=0 SKIP=0 TOTAL=1
```
"""

BEFORE_AND_AFTER = """
```yaml
models:
  my_project:
    staging:
      +materialized: view
      +schema: staging
```
```yaml
models:
  my_project:
    staging:
      +materialized: table
      +schema: staging
```
"""

TWO_UNRELATED = """
```python
import os
def read_config(path):
    return open(path).read()
```
```sql
CREATE TABLE customers (id int, name text)
```
"""


def test_error_then_fix_is_found():
    p = cp.best_pair(ERROR_THEN_FIX)
    assert p and p["kind"] == cp.ERROR_FIX
    assert "dbt0101" in p["first"]
    assert "ref(" in p["second"]


def test_command_then_output_is_found():
    p = cp.best_pair(CMD_THEN_OUTPUT)
    assert p and p["kind"] == cp.CODE_OUTPUT
    assert "dbt run" in p["first"]


def test_before_after_is_found():
    p = cp.best_pair(BEFORE_AND_AFTER)
    assert p and p["kind"] == cp.BEFORE_AFTER
    assert "view" in p["first"] and "table" in p["second"]


def test_two_unrelated_blocks_are_refused():
    """The failure that matters: two adjacent blocks are not a pair."""
    assert cp.best_pair(TWO_UNRELATED) is None


def test_prose_yields_nothing():
    assert cp.best_pair("Just an explanation with no code in it at all.") is None
    assert cp.best_pair("") is None
    assert cp.best_pair(None) is None


def test_blocks_far_apart_are_not_a_pair():
    """Adjacency is the evidence that two blocks belong together."""
    far = (ERROR_THEN_FIX.split("The fix:")[0]
           + "\n" + ("filler prose. " * 400) + "\n"
           + "```sql\nselect * from {{ ref('upstream') }}\n```")
    assert cp.best_pair(far) is None


def test_errors_outrank_everything_else():
    """A real error is the scarcest material, so it must sort first."""
    both = CMD_THEN_OUTPUT + "\n" + ERROR_THEN_FIX
    found = cp.pairs_in(both)
    assert found and found[0]["kind"] == cp.ERROR_FIX


def test_identical_blocks_are_not_before_after():
    """A block repeated verbatim has no 'what changed' to ask about."""
    same = "```yaml\nkey: value\nother: thing\n```\n" * 2
    assert cp.best_pair(same) is None


def test_prompt_block_is_imperative_and_carries_the_material():
    """Measured: DESCRIBED material was ignored in 4 of 4 turns; instructed
    material was used in 4 of 4. The block must command, and must inline."""
    block = cp.prompt_block(cp.best_pair(ERROR_THEN_FIX))
    assert "dbt0101" in block, "the material has to be inline, not referenced"
    assert "THIS TURN" in block, "the instruction has to be imperative"
    assert block.count("```") >= 2


def test_prompt_block_of_none_is_empty():
    assert cp.prompt_block(None) == ""
