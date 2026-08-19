#!/bin/sh
# Барьер между контуром производства (Harness) и остальными стеками этой машины.
#
# Зачем: на хосте живёт продакшн dos-assistant, чей Temporal (7233), Postgres
# (5432) и UI (8080) опубликованы на 0.0.0.0. Публикация делает их достижимыми
# из ЛЮБОГО контейнера через адреса хоста — включая openhands-runner, который
# исполняет сгенерированный моделью код. Сетевой изоляции docker тут мало:
# она разводит bridge-сети между собой, но не закрывает путь «контейнер → порт
# хоста», он идёт через docker-proxy и цепочку INPUT.
#
# Правила узкие по ИСТОЧНИКУ: рубится только трафик из подсети контура.
# Трафик самого dos-assistant, Traefik и внешних клиентов не затрагивается.
#
# Подсети вычисляются на ходу: `docker compose down` пересоздаёт сеть с другим
# диапазоном, и правило, прибитое к прежнему, молча защищало бы пустоту.
set -eu

# Имя docker-сети контура и порты соседей задаются снаружи: слаг compose-проекта
# у каждой установки свой, и зашивать его в репозиторий нельзя. На стенде живут
# в /etc/default/poh-harness-isolation (его читает systemd-unit рядом).
HARNESS_NET="${HARNESS_NET:-harness_default}"
FOREIGN_NETS="${FOREIGN_NETS:-}"
PORTS="${PORTS:-7233,5432,8080}"

subnet() {
	docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$1" 2>/dev/null || true
}

SRC=$(subnet "$HARNESS_NET")
if [ -z "$SRC" ]; then
	echo "сеть контура $HARNESS_NET не найдена — правила не ставлю" >&2
	exit 1
fi

# Идемпотентность: -C проверяет наличие точно такого правила, -I ставит первым.
ensure() {
	chain=$1
	shift
	iptables -C "$chain" "$@" 2>/dev/null || iptables -I "$chain" 1 "$@"
}

# 1. Прямой путь в подсети соседних стеков. Docker и так изолирует bridge-сети
#    друг от друга, но это его умолчание, а не наша гарантия.
for net in $FOREIGN_NETS; do
	dst=$(subnet "$net")
	[ -n "$dst" ] && ensure DOCKER-USER -s "$SRC" -d "$dst" -j DROP
done

# 2. Порты соседа на адресах хоста. docker0-шлюз плюс адрес, по которому машина
#    видна снаружи: сосед слушает 0.0.0.0, значит доступен по обоим.
HOST_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {print $7; exit}')
for dst in 172.17.0.1 ${HOST_IP:-}; do
	ensure DOCKER-USER -s "$SRC" -d "$dst" -p tcp -m multiport --dports "$PORTS" -j DROP
done

# 3. Тот же путь, но пойманный до FORWARD: обращение контейнера к порту хоста
#    обслуживает docker-proxy в пространстве хоста, и DOCKER-USER его не видит.
ensure INPUT -s "$SRC" -p tcp -m multiport --dports "$PORTS" -j DROP

# 4. Любая частная сеть машины, кроме собственной подсети контура. Правила выше
#    названы поимённо и стареют: сосед переедет в новую сеть или откроет ещё
#    один порт — и барьер молча перестанет что-либо значить. Контуру наружу
#    нужен только интернет (GitHub, z.ai, Sentry), поэтому RFC1918 ему закрыт
#    целиком. Исключение-RETURN стоит ПЕРВЫМ: свои postgres и temporal живут в
#    той же частной подсети, и без него контур отрезало бы от самого себя.
iptables -C DOCKER-USER -s "$SRC" -d "$SRC" -j RETURN 2>/dev/null ||
	iptables -I DOCKER-USER 1 -s "$SRC" -d "$SRC" -j RETURN
for private in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
	iptables -C DOCKER-USER -s "$SRC" -d "$private" -j DROP 2>/dev/null ||
		iptables -A DOCKER-USER -s "$SRC" -d "$private" -j DROP
done

echo "изоляция контура применена: src=$SRC ports=$PORTS"
