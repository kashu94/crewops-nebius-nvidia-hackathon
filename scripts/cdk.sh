#!/usr/bin/env bash
# Run the AWS CDK CLI with settings from .env.
#
#   scripts/cdk.sh diff
#   scripts/cdk.sh deploy --all
#   scripts/cdk.sh destroy --all
#
# .env must name an AWS CLI profile (AWS_PROFILE). Access keys live in ~/.aws, never in .env.
# The script refuses to run without a named profile, so nothing can reach a default account
# by accident, and it prints the target account before doing anything.
set -euo pipefail

cd "$(dirname "$0")/.."

# Read .env with the project's own parser (crewops.config), so this script and the Python
# code always agree on every value. Variables already set in the shell win over the file.
exports=$(uv run --quiet python -m crewops.config) || {
  echo "Could not read settings from .env (see the error above)." >&2
  exit 1
}
eval "${exports}"

export JSII_SILENCE_WARNING_DEPRECATED_NODE_VERSION=1

if [[ -z "${AWS_PROFILE:-}" || "${AWS_PROFILE}" == "default" ]]; then
  echo "Set AWS_PROFILE in .env to your named profile (for example: AWS_PROFILE=crewops)." >&2
  echo "The default profile is refused on purpose." >&2
  exit 1
fi
export AWS_PROFILE

identity=$(aws sts get-caller-identity --output text --query '[Account,Arn]') || {
  echo "Could not authenticate with profile '${AWS_PROFILE}'." >&2
  exit 1
}
read -r account arn <<<"${identity}"
echo "Profile: ${AWS_PROFILE}  Account: ${account}  Region: ${AWS_REGION}  Stage: ${CREWOPS_STAGE}"
echo "Identity: ${arn}"

exec npx --yes aws-cdk@latest "$@" --profile "${AWS_PROFILE}"
