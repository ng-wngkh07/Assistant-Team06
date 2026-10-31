#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'Missing .venv. Follow the installation steps in README.md first.'
  read '?Press Enter to close.'
  exit 1
fi
.venv/bin/python scripts/dev.py serve --profile local --require-model --open-browser
result=$?
if (( result != 0 )); then
  print 'Startup failed. Read the message above; stop any older server before restarting.'
  read '?Press Enter to close.'
fi
exit $result
