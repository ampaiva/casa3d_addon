# Changelog

## 0.8.0

- Cadastra automaticamente novos contadores de consumo individual no dashboard Energia do HA a cada 15 minutos, independentemente da planta, quadro ou fase.
- Preserva cadastros manuais e hierarquias, evita duplicatas de um mesmo dispositivo e aguarda estatísticas compatíveis quando necessário.
- Salva backup antes de atualizar pela API oficial, respeita remoções manuais e adia alterações concorrentes.
- Permite desativar novas inclusões com a opção energy_dashboard_sync.

## 0.7.3

- Usa os seis canais cadastrados dos quadros na comparação automática, excluindo representações antigas e mantendo canais esperados sem leitura como pendência.
- Unifica vínculos do catálogo e dos cartões de energia, preservando confiança e origem, com associação estável por dispositivo.
- Corrige a escala de potência do produto 16A Estimate Socket (8020 → 802 W), além da tensão; normaliza somente a análise do histórico anterior, preservando o Recorder.
- Explica pendências da identificação e trata leituras constantes como válidas até a próxima alteração ou indisponibilidade.

## 0.7.2

- Aguarda recargas lentas de integrações cloud e reconhece uma correção de tensão já concluída após expirar a conexão, evitando novas recargas desnecessárias.

## 0.7.1

- Corrige o início da sincronização no serviço s6, lendo a credencial do Supervisor no arquivo padrão preservado pela base do HA.
- Exige cobertura dos canais concorrentes antes de apresentar uma alimentação provável.

## 0.7.0

- Sincroniza cômodos Tuya com áreas do HA a cada 15 minutos, usando a conta LocalTuya existente.
- Preserva áreas atribuídas manualmente e permite escolher equivalências na tela Cômodos.
- Usa a API do HA para atualizar áreas; requer acesso homeassistant_api do complemento.
- Corrige a tensão do produto 16A Estimate Socket via quirk persistente (1250 → 125 V), sem alterar o núcleo ou o histórico do HA.
- Compara passivamente variações de potência com medidores por fase e apresenta alimentação provável quando há evidência suficiente.
- Não aciona cargas e não atribui circuito ou fase lógica sem dados; mantém vínculos explícitos prioritários.

## 0.6.0

- Vincula andares e cômodos do HA às plantas e permite marcar regiões com dois cliques.
- Descobre dispositivos ativos automaticamente, inclusive eletrodomésticos expostos somente por sensores.
- Posiciona novos dispositivos dentro do cômodo, preservando os ajustes manuais e acompanhando mudanças de área no HA.
- Agrupa consumo, potência, corrente e tensão automaticamente na visão Elétrica.
- Recupera quadro, circuito e fase quando há vínculo elétrico cadastrado ou uma medição associada ao circuito; mostra pendências quando faltam dados ou há conflitos.
- Mantém dispositivos sem cômodo vinculado na lista A localizar.
- Corrige o identificador nativo dos andares do HA.

## 0.5.0

- Declara a base Home Assistant/Alpine compatível com o serviço já usado na instalação.

- Sobrepõe os eletrodutos do projeto à planta 3D dos dois pavimentos.
- Separa piso pontilhado e teto/laje contínuo, com filtros independentes.
- Identifica as caixas de passagem e destaca seus trechos ao selecionar.
- Preserva posições e cadastro dos quadros reais no depósito.
- Permite alternar entre a planta 3D e a referência técnica.
- Inclui alinhamentos visuais para térreo v18/v20 e superior original; conferir na obra.

## 0.3.2

- Desenha spots embutidos junto aos degraus da entrada, com aro escuro e luz quente.
- Os spots acompanham o estado de Luz da Escada e acionam o mesmo canal ao clicar.
- O cabo do canal Luz da Escada termina na primeira lampada da sequencia.

## 0.1.5

- Declara `init: false`, exigido pela base s6 v3 do Home Assistant.

## 0.1.4

- Remove wrapper `with-contenv` do serviço para evitar falha de inicialização no Supervisor.

## 0.1.3

- Ajusta inicialização para o formato de serviço s6 exigido pela base do Home Assistant.

## 0.1.2

- Remove mapeamento de porta externa; o acesso passa a ser somente via Ingress.

## 0.1.1

- Corrige build local usando a imagem base do Home Assistant.

## 0.1.0

- Versão inicial do add-on Casa 3D.
- Painel via Ingress no Home Assistant.
- Base para editor de posições e futuras visões da casa.
