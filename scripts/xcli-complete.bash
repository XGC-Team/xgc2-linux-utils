# bash completion for xcli
_xcli() {
  local cur prev words cword
  if declare -F _init_completion >/dev/null 2>&1; then
    _init_completion || return
  else
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
  fi

  local domains="wifi time screen sleep cpu eval tui help"
  local wifi_v="connect disconnect restore scan status"
  local time_v="zone sync restore status"
  local screen_v="idle restore status"
  local sleep_v="off on restore status"
  local cpu_v="performance balanced restore status"
  local zones="shanghai utc tokyo seoul singapore hongkong beijing cn"

  if [[ ${COMP_CWORD} -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "${domains}" -- "${cur}") )
    return
  fi

  local domain="${COMP_WORDS[1]}"
  if [[ ${COMP_CWORD} -eq 2 ]]; then
    case "${domain}" in
      wifi) COMPREPLY=( $(compgen -W "${wifi_v}" -- "${cur}") ) ;;
      time) COMPREPLY=( $(compgen -W "${time_v}" -- "${cur}") ) ;;
      screen) COMPREPLY=( $(compgen -W "${screen_v}" -- "${cur}") ) ;;
      sleep) COMPREPLY=( $(compgen -W "${sleep_v}" -- "${cur}") ) ;;
      cpu) COMPREPLY=( $(compgen -W "${cpu_v}" -- "${cur}") ) ;;
      help) COMPREPLY=( $(compgen -W "${domains}" -- "${cur}") ) ;;
      eval|tui) COMPREPLY=( $(compgen -W "--once" -- "${cur}") ) ;;
    esac
    return
  fi

  if [[ ${COMP_CWORD} -eq 3 ]]; then
    case "${domain} ${COMP_WORDS[2]}" in
      "time zone") COMPREPLY=( $(compgen -W "${zones}" -- "${cur}") ) ;;
      "screen idle") COMPREPLY=( $(compgen -W "0 300 1800 3600" -- "${cur}") ) ;;
    esac
  fi
}

complete -F _xcli xcli
