# Simulador genérico de redes de filas

Simulador por eventos discretos desenvolvido para a disciplina de **Simulação e Métodos Analíticos**, no T1 do Módulo 8. O programa recebe uma rede de filas descrita em YAML, executa a simulação até consumir a quantidade configurada de pseudoaleatórios e informa o tempo global, os tempos e probabilidades dos estados e as perdas de cada fila.

## Integrantes

- Flávia Tavaniello
- Gustavo Trevisol
- Luísa Scolari
- Nathalie Jordão

## Evolução do projeto

Nos módulos anteriores, o grupo implementou em python um cenário específico de duas filas em tandem. Nesta etapa, o motor foi generalizado também em Python: as filas, entradas externas e probabilidades de roteamento são carregadas do arquivo YAML.

Em resposta ao feedback recebido no M6, o simulador utiliza um gerador congruencial linear com módulo maior que `2^32`:

```text
X(n+1) = (25214903917 * X(n) + 11) mod 2^48
U(n)   = X(n) / 2^48
```

Cada chamada ao gerador produz e contabiliza um único valor em `[0, 1)`. A simulação encerra depois que o último pseudoaleatório permitido é consumido.

A semente `13213`, escolhida pelo grupo e utilizada no simulador aprovado do M6, foi preservada. Também foi mantida a ordem de consumo adotada naquela implementação: em uma chegada externa, primeiro é processada a entrada do cliente e sorteado seu atendimento, quando houver servidor livre; depois é sorteado o intervalo da próxima chegada.

## Arquivos

```text
├── simulador.py
├── model.yml
├── README.md
└── T1 - Simulação_e_metodos_analíticos.docx
```

## Requisitos

- Python 3.10 ou superior;
- PyYAML.

Instalação da dependência:

```bash
python -m pip install pyyaml
```

## Execução

Na pasta do projeto, execute o modelo padrão:

```bash
python simulador.py
```

Para utilizar outro arquivo:

```bash
python simulador.py caminho/para/outro-modelo.yml
```

Para substituir a semente do YAML:

```bash
python simulador.py model.yml --seed 42
```

Se o YAML possuir várias sementes, todas podem ser executadas separadamente:

```bash
python simulador.py model.yml --all-seeds
```

Ajuda dos argumentos:

```bash
python simulador.py --help
```

## Formato do YAML

O arquivo pode começar com a tag `!PARAMETERS`, conforme o modelo da disciplina.

### Limite e sementes

```yaml
rndnumbersPerSeed: 100000
seeds:
  - 13213
```

### Filas

Cada fila deve informar servidores e intervalo de atendimento. `minArrival` e `maxArrival` são necessários apenas nas filas que recebem clientes do exterior.

```yaml
queues:
  Fila1:
    servers: 1
    capacity: -1
    minArrival: 2.0
    maxArrival: 4.0
    minService: 1.0
    maxService: 2.0
```

A capacidade infinita pode ser representada por `-1`, `null`, `inf`, `infinite` ou pela ausência de `capacity`. Capacidades positivas representam o total máximo de clientes na fila, incluindo clientes em atendimento.

### Primeiras chegadas externas

```yaml
arrivals:
  Fila1: 2.0
```

É possível informar mais de uma fila de entrada. Depois da primeira chegada, o intervalo seguinte é sorteado entre `minArrival` e `maxArrival` da fila correspondente.

### Roteamento

```yaml
network:
  - source: Fila1
    target: Fila2
    probability: 0.2
  - source: Fila1
    target: Fila3
    probability: 0.8
  - source: Fila2
    target: Out
    probability: 1.0
```

Todas as possibilidades de roteamento devem ser declaradas. Para cada fila de origem, a soma das probabilidades deve ser exatamente `1`. A saída do sistema deve aparecer explicitamente usando `target: Out` ou `target: null`. Mesmo uma fila que encaminhe todos os clientes ao exterior precisa de uma rota com probabilidade `1.0`.

## Funcionamento da simulação

O simulador mantém os eventos futuros em uma fila de prioridades. Em caso de eventos no mesmo instante, aplica a ordem:

1. término de atendimento;
2. passagem entre filas;
3. chegada externa.

Ao terminar um atendimento, o cliente é encaminhado conforme as probabilidades da fila de origem. Uma passagem é uma movimentação interna da rede, não uma nova chegada externa, portanto não agenda outra chegada.

Quando existe uma única rota com probabilidade `1.0`, o destino é determinístico e nenhum pseudoaleatório de roteamento é consumido, preservando o comportamento do simulador de filas em tandem aprovado no M6. Quando há duas ou mais possibilidades, um pseudoaleatório é sorteado e comparado às faixas acumuladas de probabilidade.

Antes de começar, o programa valida nomes de filas, destinos, servidores, capacidades, intervalos e probabilidades. Erros de configuração são apresentados no terminal com uma mensagem explicativa.

## Saída

Para cada execução, o programa informa:

- arquivo e semente utilizados;
- gerador pseudoaleatório;
- tempo global;
- quantidade exata de pseudoaleatórios consumidos;
- tempos acumulados de todos os estados;
- probabilidades dos estados;
- soma dos tempos e das probabilidades;
- clientes perdidos por fila.

O `model.yml` incluído representa a rede obrigatória do T1: Fila 1 com entrada externa e roteamento para as Filas 2 e 3, incluindo retornos e saídas probabilísticas.
