#!/bin/sh
# Stands in for install.sh when the survey drives the web installer: says what install.sh says, without installing,
# and points at the Lens the survey already started.
b=$(printf '\033[1m'); r=$(printf '\033[0m')
echo "${b}Checking this computer${r}"
echo "${b}Getting Lens${r}"
echo "${b}Building and starting${r}"
echo "Lens is running: ${LENS_SURVEY_SETUP_URL:-http://localhost:3000/setup}"
