#!/bin/sh
# The native deAPI job pattern with curl, one step per call.
#
#   export DEAPI_API_KEY=sk-dp-...
#   sh native_job.sh models txt2img          # models this key may use for a task (free)
#   sh native_job.sh quote                   # price of the job below (free)
#   sh native_job.sh submit                  # start it (paid) -> {"data":{"request_id":"..."}}
#   sh native_job.sh status REQUEST_ID       # pending | processing | done | error
#   sh native_job.sh download URL FILE       # save result_url (no Authorization header)
#
# Edit BODY for your job; take sizes and steps from the model card (info.limits, info.defaults).
# DEAPI_API_URL (default https://api.deapi.ai) may name another *.deapi.ai host or a local mock.
set -eu

API="${DEAPI_API_URL:-https://api.deapi.ai}"
API="${API%/}"
scheme="${API%%://*}"
host="${API#*://}"; host="${host%%/*}"; host="${host%%:*}"
case "$scheme:$host" in
  *@*) echo "refusing a URL with credentials" >&2; exit 2 ;;
  https:deapi.ai | https:*.deapi.ai | http:127.0.0.1 | http:localhost) ;;
  *) echo "refusing to send the API key to $host" >&2; exit 2 ;;
esac
: "${DEAPI_API_KEY:?set DEAPI_API_KEY}"

BODY='{"prompt": "a red fox in the snow", "model": "Flux1schnell", "width": 512, "height": 512, "steps": 4, "seed": 42}'
ENDPOINT="/api/v2/images/generations"

# The key goes in a header read from stdin, so it does not show up in the process list.
api() {
  method="$1"; path="$2"; shift 2
  printf 'Authorization: Bearer %s\n' "$DEAPI_API_KEY" | curl -sS --fail-with-body \
    -X "$method" "$API$path" \
    -H @- -H "Accept: application/json" -H "User-Agent: deapi-example/2.0" "$@"
}

case "${1:-}" in
  models)
    api GET "/api/v2/models" -G --data-urlencode "filter[inference_types]=${2:-txt2img}" \
      --data-urlencode "limit=100" ;;
  quote)
    api POST "$ENDPOINT/price" -H "Content-Type: application/json" -d "$BODY" ;;
  submit)
    api POST "$ENDPOINT" -H "Content-Type: application/json" -d "$BODY" ;;
  status)
    api GET "/api/v2/jobs/${2:?request id}" ;;
  download)
    # Pre-signed link: no Authorization header, and never overwrite an existing file.
    [ -e "${3:?output file}" ] && { echo "$3 exists" >&2; exit 2; }
    curl -sS --fail -A "deapi-example/2.0" -o "$3" "${2:?result url}" ;;
  *)
    sed -n '2,13p' "$0"; exit 2 ;;
esac
echo
