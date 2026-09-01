"""Detection use cases: poll, roll up, evaluate, and say what changed.

The domain decides; this package does the I/O and the publishing. Three things
live here and nowhere else:

- the **metric poller**, which turns one scrape of an application's frozen
  `/observe/metrics` contract into five-second rollups;
- the **probe runner**, which exercises each declared journey in each declared
  region and records what it saw - including "could not check", which is not the
  same as "failed";
- the **detection service**, which runs the pure evaluation and publishes only
  what *changed*. That last word matters: a five-second loop that published a
  verdict every tick would put seventeen thousand events a day per application
  on a timeline meant to be read by a person.
"""
