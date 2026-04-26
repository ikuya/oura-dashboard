#!/bin/bash

export PATH=$HOME/.local/bin:$PATH

WORK_DIR=$HOME/work/oura-dashboard/

cd $WORK_DIR
uv run python daily_sync.py >> $WORK_DIR/log/oura-daily-sync.log 2>&1

