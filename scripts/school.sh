# School selection for `make start`. Sourced after common.sh; written for the bash 3.2 that ships with macOS.

SCHOOL_IDS=()
SCHOOL_NAMES=()

# The list comes from the school registry, so a school added to seed.py shows up here (tests/test_seed.py checks this parser).
load_schools() {
  local id name
  while IFS=$'\t' read -r id name; do
    SCHOOL_IDS+=("$id")
    SCHOOL_NAMES+=("$name")
  done < <(awk -F'"' '
    /^SCHOOLS = \{/ { inside = 1; next }
    inside && /^}/ { inside = 0 }
    inside && /^    "[^"]+": \{/ { id = $2 }
    inside && /^        "name": "/ { printf "%s\t%s\n", id, $4 }
  ' apps/school-data/app/seed.py)
  [ "${#SCHOOL_IDS[@]}" -gt 0 ] || fail "Nenhuma escola encontrada em apps/school-data/app/seed.py."
}

school_index() {
  local i
  for i in "${!SCHOOL_IDS[@]}"; do
    if [ "${SCHOOL_IDS[$i]}" = "$1" ]; then echo "$i"; return 0; fi
  done
  return 1
}

# Arrow keys (or j/k), a number, Enter to confirm, q to cancel. Sets CHOSEN_INDEX.
school_menu() {
  local selected="$1" count="${#SCHOOL_IDS[@]}" first=1 i key rest
  trap 'printf "\033[?25h\n"; exit 130' INT
  printf '\033[?25l\n\033[1mCom qual escola você quer trabalhar?\033[0m\n'
  printf '\033[2m↑/↓ ou número para escolher · Enter para confirmar · q para cancelar\033[0m\n\n'
  while :; do
    [ "$first" = 1 ] || printf '\033[%dA' "$count"
    first=0
    for i in "${!SCHOOL_IDS[@]}"; do
      if [ "$i" = "$selected" ]; then
        printf '\033[2K\033[36m ❯ %d. %s\033[0m\n' "$((i + 1))" "${SCHOOL_NAMES[$i]}"
      else
        printf '\033[2K   %d. %s\n' "$((i + 1))" "${SCHOOL_NAMES[$i]}"
      fi
    done
    IFS= read -rsn1 key || key=q
    case "$key" in
      '') break ;;
      q|Q) printf '\033[?25h'; fail "Cancelado." ;;
      k) selected=$(((selected + count - 1) % count)) ;;
      j) selected=$(((selected + 1) % count)) ;;
      [1-9]) if [ "$key" -le "$count" ]; then selected=$((key - 1)); fi ;;
      $'\033')
        IFS= read -rsn2 -t 1 rest || rest=''
        case "$rest" in
          '[A'|'OA') selected=$(((selected + count - 1) % count)) ;;
          '[B'|'OB') selected=$(((selected + 1) % count)) ;;
        esac ;;
    esac
  done
  trap - INT
  printf '\033[?25h\n'
  CHOSEN_INDEX="$selected"
}

# Sets and exports the active school. Priority: SCHOOL=<id>, then the menu, then (without a terminal) the last school used.
choose_school() {
  load_schools
  local last index=0 wanted="${SCHOOL:-}"
  last="$(env_value APOLO_SCHOOL)"
  if [ -n "$wanted" ]; then
    index="$(school_index "$wanted")" || fail "Escola '$wanted' não existe. Opções: ${SCHOOL_IDS[*]}"
  elif [ -t 0 ] && [ -t 1 ]; then
    school_index "$last" >/dev/null && index="$(school_index "$last")"
    school_menu "$index"
    index="$CHOSEN_INDEX"
  else
    index="$(school_index "$last")" || fail "Sem terminal para perguntar a escola. Use: make start SCHOOL=<${SCHOOL_IDS[*]}>"
  fi
  APOLO_SCHOOL="${SCHOOL_IDS[$index]}"
  APOLO_SCHOOL_NAME="${SCHOOL_NAMES[$index]}"
  COMPOSE_PROJECT_NAME="$PROJECT_PREFIX-$APOLO_SCHOOL"
  export APOLO_SCHOOL APOLO_SCHOOL_NAME COMPOSE_PROJECT_NAME
  # Kept in .env so make stop/logs/ps and manual `docker compose` commands target this school's stack.
  set_env_value APOLO_SCHOOL "$APOLO_SCHOOL"
  set_env_value COMPOSE_PROJECT_NAME "$COMPOSE_PROJECT_NAME"
}
