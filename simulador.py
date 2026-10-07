"""Simulador genérico de redes de filas por eventos discretos."""

from __future__ import annotations

import argparse
import heapq
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


EXIT = "Out"
EVENT_PRIORITY = {"DEPARTURE": 0, "PASSAGE": 1, "ARRIVAL": 2}


class ConfigurationError(ValueError):
    """Erro de validação do arquivo de entrada."""


class RandomManager:
    """LCG de 48 bits e contador estrito de números consumidos.

    Evolução solicitada após o M2: o módulo 2^48 é maior que 2^32.
    Cada chamada a ``next`` consome exatamente um pseudoaleatório.
    """

    MULTIPLIER = 25_214_903_917
    INCREMENT = 11
    MODULUS = 2**48

    def __init__(self, seed: int, max_randoms: int):
        self.state = int(seed) % self.MODULUS
        self.max_randoms = int(max_randoms)
        self.count = 0

    def next(self) -> float | None:
        if self.count >= self.max_randoms:
            return None
        self.state = (
            self.MULTIPLIER * self.state + self.INCREMENT
        ) % self.MODULUS
        self.count += 1
        return self.state / self.MODULUS

    def uniform(self, minimum: float, maximum: float) -> float | None:
        value = self.next()
        if value is None:
            return None
        return minimum + (maximum - minimum) * value


@dataclass
class QueueState:
    name: str
    servers: int
    capacity: int | None
    min_service: float
    max_service: float
    min_arrival: float | None = None
    max_arrival: float | None = None
    in_system: int = 0
    losses: int = 0
    times: dict[int, float] = field(default_factory=dict)
    last_update: float = 0.0

    @property
    def has_space(self) -> bool:
        return self.capacity is None or self.in_system < self.capacity

    def update_time(self, current_time: float) -> None:
        elapsed = current_time - self.last_update
        if elapsed < -1e-12:
            raise RuntimeError("A lista de eventos deixou de estar em ordem temporal.")
        self.times[self.in_system] = self.times.get(self.in_system, 0.0) + max(
            elapsed, 0.0
        )
        self.last_update = current_time


class ParametersLoader(yaml.SafeLoader):
    """Loader seguro que aceita a tag !PARAMETERS dos modelos da disciplina."""


def _construct_tagged_value(
    loader: ParametersLoader, _tag_suffix: str, node: yaml.Node
) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


ParametersLoader.add_multi_constructor("!", _construct_tagged_value)


def _parse_capacity(value: Any, queue_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {
        "inf",
        "infinite",
        "infinita",
        "infinito",
        "infinity",
        "∞",
    }:
        return None
    if isinstance(value, bool):
        raise ConfigurationError(f"Capacidade inválida em {queue_name}: {value!r}.")
    try:
        capacity = int(value)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            f"Capacidade inválida em {queue_name}: {value!r}."
        ) from error
    if capacity == -1:
        return None
    if capacity <= 0:
        raise ConfigurationError(
            f"A capacidade de {queue_name} deve ser positiva, -1 ou null."
        )
    return capacity


class NetworkSimulator:
    def __init__(self, config_path: str | Path, seed_override: int | None = None):
        self.config_path = Path(config_path)
        self.seed_override = seed_override
        self.queues: dict[str, QueueState] = {}
        self.network: dict[str, list[tuple[str, float]]] = {}
        self.events: list[tuple[float, int, int, str, str | None, str]] = []
        self.event_counter = 0
        self.current_time = 0.0
        self.random: RandomManager | None = None
        self.seed: int | None = None
        self.max_randoms = 0
        self.max_state_seen: dict[str, int] = {}

    @staticmethod
    def read_config(config_path: str | Path) -> dict[str, Any]:
        path = Path(config_path)
        if not path.is_file():
            raise ConfigurationError(f"Arquivo de configuração não encontrado: {path}")
        try:
            with path.open("r", encoding="utf-8") as file:
                data = yaml.load(file, Loader=ParametersLoader)
        except yaml.YAMLError as error:
            raise ConfigurationError(f"YAML inválido em {path}: {error}") from error
        if not isinstance(data, dict):
            raise ConfigurationError("A raiz do YAML deve ser um mapeamento.")
        return data

    @staticmethod
    def configured_seeds(config_path: str | Path) -> list[int]:
        data = NetworkSimulator.read_config(config_path)
        seeds = data.get("seeds", [1])
        if not isinstance(seeds, list) or not seeds:
            raise ConfigurationError("'seeds' deve ser uma lista não vazia.")
        try:
            return [int(seed) for seed in seeds]
        except (TypeError, ValueError) as error:
            raise ConfigurationError("Todas as sementes devem ser números inteiros.") from error

    def load_config(self) -> None:
        data = self.read_config(self.config_path)
        queues_data = data.get("queues")
        if not isinstance(queues_data, dict) or not queues_data:
            raise ConfigurationError("O YAML deve conter ao menos uma fila em 'queues'.")

        seeds = data.get("seeds", [1])
        if not isinstance(seeds, list) or not seeds:
            raise ConfigurationError("'seeds' deve ser uma lista não vazia.")
        try:
            self.seed = (
                self.seed_override
                if self.seed_override is not None
                else int(seeds[0])
            )
            self.max_randoms = int(data.get("rndnumbersPerSeed", 100_000))
        except (TypeError, ValueError) as error:
            raise ConfigurationError(
                "A semente e 'rndnumbersPerSeed' devem ser números inteiros."
            ) from error
        if self.max_randoms <= 0:
            raise ConfigurationError("'rndnumbersPerSeed' deve ser maior que zero.")
        self.random = RandomManager(self.seed, self.max_randoms)

        for queue_name, queue_data in queues_data.items():
            if not isinstance(queue_name, str) or not queue_name.strip():
                raise ConfigurationError("Toda fila deve possuir um nome textual.")
            if not isinstance(queue_data, dict):
                raise ConfigurationError(f"A configuração de {queue_name} é inválida.")
            try:
                servers = int(queue_data["servers"])
                min_service = float(queue_data["minService"])
                max_service = float(queue_data["maxService"])
            except KeyError as error:
                raise ConfigurationError(
                    f"Campo obrigatório ausente em {queue_name}: {error.args[0]}."
                ) from error
            except (TypeError, ValueError) as error:
                raise ConfigurationError(
                    f"Parâmetro numérico inválido em {queue_name}."
                ) from error

            capacity = _parse_capacity(queue_data.get("capacity"), queue_name)
            try:
                min_arrival = queue_data.get("minArrival")
                max_arrival = queue_data.get("maxArrival")
                min_arrival = None if min_arrival is None else float(min_arrival)
                max_arrival = None if max_arrival is None else float(max_arrival)
            except (TypeError, ValueError) as error:
                raise ConfigurationError(
                    f"Intervalo de chegada inválido em {queue_name}."
                ) from error

            if servers <= 0:
                raise ConfigurationError(f"{queue_name} deve ter ao menos um servidor.")
            if capacity is not None and capacity < servers:
                raise ConfigurationError(
                    f"A capacidade de {queue_name} não pode ser menor que seus servidores."
                )
            if min_service < 0 or max_service < min_service:
                raise ConfigurationError(
                    f"Intervalo de atendimento inválido em {queue_name}."
                )
            if (min_arrival is None) != (max_arrival is None):
                raise ConfigurationError(
                    f"{queue_name} deve informar minArrival e maxArrival em conjunto."
                )
            if min_arrival is not None and (
                min_arrival < 0 or max_arrival < min_arrival
            ):
                raise ConfigurationError(
                    f"Intervalo de chegada inválido em {queue_name}."
                )

            self.queues[queue_name] = QueueState(
                name=queue_name,
                servers=servers,
                capacity=capacity,
                min_service=min_service,
                max_service=max_service,
                min_arrival=min_arrival,
                max_arrival=max_arrival,
            )
            self.max_state_seen[queue_name] = 0

        network_data = data.get("network", [])
        if network_data is None:
            network_data = []
        if not isinstance(network_data, list):
            raise ConfigurationError("'network' deve ser uma lista de rotas.")

        probability_sums: dict[str, float] = {}
        for index, route in enumerate(network_data, start=1):
            if not isinstance(route, dict):
                raise ConfigurationError(f"Rota {index} inválida.")
            try:
                source = route["source"]
                target_value = route.get("target")
                probability = float(route["probability"])
            except (KeyError, TypeError, ValueError) as error:
                raise ConfigurationError(f"Rota {index} inválida: {route!r}.") from error
            if source not in self.queues:
                raise ConfigurationError(f"A origem da rota {index} não existe: {source}.")
            target = (
                EXIT
                if target_value is None
                or str(target_value).strip().lower() in {"out", "exit", "saida", "saída"}
                else str(target_value)
            )
            if target != EXIT and target not in self.queues:
                raise ConfigurationError(f"O destino da rota {index} não existe: {target}.")
            if not 0.0 <= probability <= 1.0:
                raise ConfigurationError(
                    f"A probabilidade da rota {index} deve estar entre 0 e 1."
                )
            self.network.setdefault(source, []).append((target, probability))
            probability_sums[source] = probability_sums.get(source, 0.0) + probability

        for queue_name in self.queues:
            total = probability_sums.get(queue_name, 0.0)
            if abs(total - 1.0) > 1e-12:
                raise ConfigurationError(
                    f"As probabilidades de saída de {queue_name} somam "
                    f"{total:.6f}; a soma deve ser exatamente 1."
                )

        arrivals = data.get("arrivals", {})
        if not isinstance(arrivals, dict) or not arrivals:
            raise ConfigurationError(
                "'arrivals' deve informar ao menos uma primeira chegada externa."
            )
        for queue_name, first_time_value in arrivals.items():
            if queue_name not in self.queues:
                raise ConfigurationError(
                    f"A chegada externa referencia uma fila inexistente: {queue_name}."
                )
            queue = self.queues[queue_name]
            if queue.min_arrival is None:
                raise ConfigurationError(
                    f"{queue_name} recebe chegadas externas, mas não possui intervalo de chegada."
                )
            try:
                first_time = float(first_time_value)
            except (TypeError, ValueError) as error:
                raise ConfigurationError(
                    f"Primeira chegada inválida para {queue_name}."
                ) from error
            if first_time < 0:
                raise ConfigurationError("O tempo da primeira chegada não pode ser negativo.")
            self.schedule_event(first_time, "ARRIVAL", source=None, target=queue_name)

    def schedule_event(
        self, time: float, event_type: str, source: str | None, target: str
    ) -> None:
        self.event_counter += 1
        heapq.heappush(
            self.events,
            (
                float(time),
                EVENT_PRIORITY[event_type],
                self.event_counter,
                event_type,
                source,
                target,
            ),
        )

    def choose_route(self, queue_name: str) -> str | None:
        routes = self.network.get(queue_name, [])
        if not routes:
            return EXIT
        # Uma única rota com probabilidade 1 é determinística e não exige
        # sorteio. Isso preserva o comportamento do simulador aprovado no M6.
        if len(routes) == 1 and abs(routes[0][1] - 1.0) <= 1e-12:
            return routes[0][0]
        value = self.random.next()
        if value is None:
            return None
        cumulative = 0.0
        for target, probability in routes:
            cumulative += probability
            if value < cumulative:
                return target
        raise RuntimeError(
            f"Nenhuma faixa de roteamento encontrada para {queue_name}; "
            "verifique a soma das probabilidades."
        )

    def _schedule_service(self, queue: QueueState) -> None:
        service_time = self.random.uniform(queue.min_service, queue.max_service)
        if service_time is not None:
            self.schedule_event(
                self.current_time + service_time,
                "DEPARTURE",
                source=queue.name,
                target=queue.name,
            )

    def _receive_customer(self, queue: QueueState) -> None:
        if not queue.has_space:
            queue.losses += 1
            return
        queue.in_system += 1
        self.max_state_seen[queue.name] = max(
            self.max_state_seen[queue.name], queue.in_system
        )
        if queue.in_system <= queue.servers:
            self._schedule_service(queue)

    def run(self) -> None:
        self.load_config()
        while self.events and self.random.count < self.random.max_randoms:
            (
                event_time,
                _,
                _,
                event_type,
                source,
                target,
            ) = heapq.heappop(self.events)

            for queue in self.queues.values():
                queue.update_time(event_time)
            self.current_time = event_time
            queue = self.queues[target]

            if event_type == "ARRIVAL":
                # Mantém a ordem de consumo adotada no M6 aprovado:
                # primeiro processa a entrada e, se necessário, sorteia o
                # atendimento; depois sorteia o intervalo da próxima chegada.
                self._receive_customer(queue)
                interval = self.random.uniform(queue.min_arrival, queue.max_arrival)
                if interval is not None:
                    self.schedule_event(
                        self.current_time + interval,
                        "ARRIVAL",
                        source=None,
                        target=target,
                    )
            elif event_type == "PASSAGE":
                self._receive_customer(queue)
            elif event_type == "DEPARTURE":
                if queue.in_system <= 0:
                    raise RuntimeError(f"Saída inválida detectada em {queue.name}.")
                queue.in_system -= 1
                if queue.in_system >= queue.servers:
                    self._schedule_service(queue)
                destination = self.choose_route(queue.name)
                if destination not in {None, EXIT}:
                    self.schedule_event(
                        self.current_time,
                        "PASSAGE",
                        source=queue.name,
                        target=destination,
                    )

        for queue in self.queues.values():
            queue.update_time(self.current_time)

    def print_results(self, run_number: int | None = None) -> None:
        if run_number is not None:
            print(f"\nEXECUÇÃO {run_number}")
        print("=" * 74)
        print("RESULTADOS DA SIMULAÇÃO DE REDE DE FILAS")
        print("=" * 74)
        print(f"Arquivo de entrada: {self.config_path}")
        print(f"Semente utilizada: {self.seed}")
        print(f"Gerador: LCG (a={RandomManager.MULTIPLIER}, c=11, M=2^48)")
        print(f"Tempo global da simulação: {self.current_time:.6f}")
        print(f"Pseudoaleatórios consumidos: {self.random.count}")

        for queue_name, queue in self.queues.items():
            capacity = "infinita" if queue.capacity is None else str(queue.capacity)
            print(
                f"\n{queue_name} - servidores: {queue.servers}; capacidade: {capacity}"
            )
            print(f"Clientes perdidos: {queue.losses}")
            print("Estado | Tempo acumulado | Probabilidade")
            print("-" * 46)
            last_state = (
                queue.capacity
                if queue.capacity is not None
                else self.max_state_seen[queue_name]
            )
            probability_sum = 0.0
            time_sum = 0.0
            for state in range(last_state + 1):
                accumulated = queue.times.get(state, 0.0)
                probability = (
                    accumulated / self.current_time * 100.0
                    if self.current_time > 0
                    else 0.0
                )
                probability_sum += probability
                time_sum += accumulated
                print(f"{state:>6} | {accumulated:>15.6f} | {probability:>11.6f}%")
            print("-" * 46)
            print(f"Soma dos tempos: {time_sum:.6f}")
            print(f"Soma das probabilidades: {probability_sum:.6f}%")


def parse_arguments() -> argparse.Namespace:
    default_model = Path(__file__).resolve().with_name("model.yml")
    parser = argparse.ArgumentParser(
        description="Simula uma rede de filas configurada em YAML."
    )
    parser.add_argument(
        "model",
        nargs="?",
        default=default_model,
        help="caminho do arquivo YAML (padrão: model.yml ao lado do simulador)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="substitui a primeira semente informada no YAML",
    )
    parser.add_argument(
        "--all-seeds",
        action="store_true",
        help="executa uma simulação independente para cada semente do YAML",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        seeds = (
            NetworkSimulator.configured_seeds(args.model)
            if args.all_seeds
            else [args.seed]
        )
        for run_number, seed in enumerate(seeds, start=1):
            simulator = NetworkSimulator(args.model, seed_override=seed)
            simulator.run()
            simulator.print_results(run_number if len(seeds) > 1 else None)
        return 0
    except (ConfigurationError, OSError, ValueError) as error:
        print(f"Erro de configuração: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
