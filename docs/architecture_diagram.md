# DCAF Architecture Diagram

This page is presentation-oriented. It shows the main public types, how they relate, and what the current library shape suggests about project maturity.

## Class Diagram

```mermaid
classDiagram
    direction TB

    class BaseStream~EntryT~ {
      <<internal>>
      +entries: list[EntryT]
      +from_streams(...)
      +append(entry)
      +extend(other)
      +apply(fn)
      +flat_apply(fn)
      +filter_apply(fn)
      +sort(...)
      +sum()
      +count()
    }

    class BaseGroup~KeyT,EntryT,StreamT~ {
      <<internal>>
      +groups: dict[KeyT, StreamT]
      +aggregate(fn)
      +apply_to_groups(fn, keys)
      +filter_groups(fn)
      +ungroup()
    }

    class CashFlow {
      <<dataclass>>
      +amount: float
      +date: date
      +label: str
      +is_cash: bool
      +tags: frozenset[CashFlowTags]
      +has_tag(tag)
      +replace(...)
      +to_stream()
    }

    class CashFlowStream {
      <<public>>
      +from_recurring(...)
      +from_streams(...)
      +group_by(...)
      +npv(rate, valuation_date)
      +filter(...)
      +inflows()
      +outflows()
    }

    class CashFlowGroup~KeyT~ {
      <<public>>
      +aggregate(fn)
      +apply_to_groups(fn, keys)
      +sum()
      +count()
    }

    class CashFlowTags {
      <<enum>>
      REVENUE
      EXPENSE
      TAXABLE
      TAX_DEDUCTIBLE
      CAPEX
      OPEX
      DEPRECIATION
      DEBT_INTEREST
      DEBT_PRINCIPAL
    }

    class Generation {
      <<dataclass>>
      +amount_mwh: float
      +date: date
      +label: str
    }

    class GenerationStream {
      <<public>>
      +from_capacity(...)
      +from_streams(...)
      +group_by(...)
      +discounted_sum(...)
      +to_revenue(...)
      +to_cost(...)
    }

    class GenerationGroup~KeyT~ {
      <<public>>
      +aggregate(fn)
      +apply_to_groups(fn, keys)
      +sum()
      +count()
    }

    class EscalationPolicy {
      <<protocol>>
      +reference_date: date
      +factor(target_date)
    }

    class ConstantRateEscalation {
      <<dataclass>>
      +reference_date: date
      +rate: float
      +period: Period
      +factor(target_date)
    }

    class IndexSeriesEscalation {
      <<dataclass>>
      +reference_date: date
      +points: IndexSeries
      +factor(target_date)
    }

    class EscalationSegment {
      <<dataclass>>
      +start_date: date
      +policy: EscalationPolicy
    }

    class CompositeEscalation {
      <<dataclass>>
      +reference_date: date
      +segments: tuple[EscalationSegment]
      +factor(target_date)
    }

    class EscalationBuilder {
      <<dataclass>>
      +then_constant(...)
      +then_index(...)
      +build()
    }

    class SpendProfile {
      <<dataclass>>
      +schedule: SpendSchedule
      +name: str
      +curve(name)
      +custom(schedule)
    }

    class ConstructionFinancing {
      <<dataclass>>
      +debt_fraction: float
      +interest_rate: float
      +interest_treatment: InterestTreatment
      +servicing_period: Period
      +debt(...)
    }

    class ConstructionSpendConfig {
      <<dataclass>>
      +total_cost: float
      +start_date: date
      +end_date: date
      +period: Period
      +profile: SpendProfile
      +financing: ConstructionFinancing
      +escalation: float
    }

    class ConstructionSpendBuilder {
      <<public>>
      -_config: ConstructionSpendConfig
      -_escalation_policy: EscalationPolicy
      +from_config(config)
      +profile(...)
      +curve(name)
      +schedule(...)
      +financing(...)
      +escalation(...)
      +build()
    }

    class AmortizationSchedule {
      <<public>>
      +total: CashFlowStream
      +interest: CashFlowStream
      +principal: CashFlowStream
      +builder(...)
      +build(...)
    }

    class AmortizationBuilder {
      <<public>>
      +interest_only(periods)
      +interest_free(...)
      +rate_change(...)
      +build()
    }

    BaseStream <|-- CashFlowStream
    BaseStream <|-- GenerationStream
    BaseGroup <|-- CashFlowGroup
    BaseGroup <|-- GenerationGroup

    CashFlowStream *-- CashFlow : contains
    CashFlowGroup o-- CashFlowStream : groups
    CashFlow --> CashFlowTags : tagged by

    GenerationStream *-- Generation : contains
    GenerationGroup o-- GenerationStream : groups

    EscalationPolicy <|.. ConstantRateEscalation
    EscalationPolicy <|.. IndexSeriesEscalation
    EscalationPolicy <|.. CompositeEscalation
    CompositeEscalation *-- EscalationSegment
    EscalationSegment --> EscalationPolicy
    EscalationBuilder --> CompositeEscalation : builds
    EscalationBuilder --> EscalationSegment : assembles

    CashFlowStream ..> EscalationPolicy : recurring escalation
    GenerationStream ..> EscalationPolicy : price/cost escalation
    ConstructionSpendBuilder ..> EscalationPolicy : optional override
    ConstructionSpendBuilder *-- ConstructionSpendConfig
    ConstructionSpendConfig *-- SpendProfile
    ConstructionSpendConfig *-- ConstructionFinancing
    ConstructionSpendBuilder --> CashFlowStream : builds

    AmortizationSchedule *-- CashFlowStream : total
    AmortizationSchedule *-- CashFlowStream : interest
    AmortizationSchedule *-- CashFlowStream : principal
    AmortizationBuilder --> AmortizationSchedule : builds

    GenerationStream --> CashFlowStream : to_revenue / to_cost
```

## Usage Flow

```mermaid
flowchart LR
    A[GenerationStream.from_capacity] --> B[to_revenue]
    A --> C[to_cost]
    B --> D[Revenue CashFlowStream]
    C --> E[Variable OPEX CashFlowStream]

    F[fixed_opex] --> G[Fixed OPEX CashFlowStream]
    H[ConstructionSpendBuilder.build] --> I[Construction CAPEX CashFlowStream]
    J[AmortizationBuilder.build] --> K[AmortizationSchedule]
    K --> L[Debt Service CashFlowStreams]

    I --> M[itc / itc_adjusted_basis]
    I --> N[macrs_schedule / vdb_schedule]
    A --> O[ptc]
    X[Custom modifiers / user-defined transformations] --> D
    X --> E
    X --> G
    X --> I
    X --> N
    X --> O

    D --> P[compute_taxable_income]
    E --> P
    G --> P
    N --> P
    L --> P
    O --> P
    Y[Custom tax incentives / custom tax schedule logic] --> P

    P --> Q[Taxable Income CashFlowStream]
    Q --> R[tax_liability]

    D --> S[CashFlowStream.from_streams]
    E --> S
    G --> S
    I --> S
    L --> S
    O --> S
    M --> S
    R --> S

    S --> T[Project CashFlowStream]
    T --> U[npv]
    T --> V[IRR / XIRR metric calculation]
    A --> W[discounted_sum]
    T --> Z[LCOE calculation]
    W --> Z
```

## Slide-Friendly Usage View

```mermaid
flowchart LR
    U[User-Provided Case Parameters]
    A[Physical Model<br/>GenerationStream] --> B[Operational Economics<br/>Revenue and Variable Cost]
    C[Project Buildout<br/>Construction CAPEX and Debt] --> D[Tax Layer<br/>Depreciation, Incentives, Taxable Income, Tax Liability]
    B --> E[Project CashFlowStream]
    C --> E
    D --> E
    F[Custom Modifiers<br/>Custom incentives, tax logic, stream transforms] --> B
    F --> C
    F --> D
    A --> G[LCOE Denominator<br/>discounted_sum of MWh]
    E --> H[Project Metrics<br/>NPV and IRR/XIRR]
    E --> I[LCOE Numerator<br/>discounted project costs]
    E --> P[Economics Pro-Forma]
    G --> J[LCOE]
    I --> J

    classDef inputs fill:#e7edf3,stroke:#4c6475,color:#16202a,stroke-width:1.5px;
    classDef core fill:#f4efe3,stroke:#8d7b4c,color:#2b2417,stroke-width:1.5px;
    classDef custom fill:#e6f4ea,stroke:#4f7a57,color:#18311e,stroke-width:1.5px;
    classDef metric fill:#ffd88a,stroke:#8a5a00,color:#2f2000,stroke-width:2px;

    class U inputs;
    class A,B,C,D,E,G,I core;
    class F custom;
    class H,J,P metric;
```

Use this version when you want to emphasize the architecture in six ideas instead of many concrete functions:

- `Physical Model`: generation is modeled separately from money.
- `Operational Economics`: generation is converted into revenues and variable operating costs.
- `Project Buildout`: construction spend and financing create CAPEX and debt-service streams.
- `Tax Layer`: depreciation and incentives affect taxable income and tax liability.
- `Custom Modifiers`: the library is designed to accept additional user-defined transformations by composing streams.
- `Project Metrics`: the assembled project stream drives valuation metrics, while LCOE also depends on discounted generation.

## Current State

- The core library shape is stable and modular: `CashFlow` and `Generation` are the atomic records, while `CashFlowStream` and `GenerationStream` are the main composition layer.
- Builder usage is selective and intentional. More stateful domains use builders (`ConstructionSpendBuilder`, `AmortizationBuilder`); simpler domains stay as pure functions (`fixed_opex`, `ptc`, `itc`, `tax_liability`, depreciation helpers).
- Escalation is one of the strongest design abstractions in the codebase. A shared `EscalationPolicy` protocol is reused across recurring cashflows, generation pricing, and construction spend.
- The repo currently looks like a well-tested financial toolkit rather than a single top-level project model or pro-forma engine. That is an inference from the public API shape: many composable primitives, no central `Project` or `Model` aggregate object.
- Extension points exist mostly through composition rather than subclassing. In practice, “custom modifiers” are additional functions or stream transformations that produce or modify `CashFlowStream` objects before they are merged into the project model.
- Depreciation is modeled explicitly as non-cash `CashFlowStream` output (`macrs_schedule`, `vdb_schedule`) that feeds taxable income rather than project cash NPV directly.
- LCOE is supported conceptually by the current primitives: use the project-level cost cashflows as the numerator and `GenerationStream.discounted_sum()` as the discounted MWh denominator.
- IRR is listed in the repo requirements/readme, but there is no public `irr()` helper in the current library code. For presentation purposes, it is best shown as a metric calculated from `Project CashFlowStream`, but it appears to still be an integration-layer or roadmap item rather than a shipped API.
- Test maturity is good for this layer: `uv run pytest -q` currently passes with `408 passed`, and the tests are spread across cashflows, generation, construction, escalation, depreciation, tax incentives, tax liability, and amortization.

## Presentation Framing

- If you need to explain the design in one sentence: DCAF is built around immutable financial and physical records, typed stream containers, reusable escalation policies, and builder APIs only where configuration complexity justifies them.
- If you need to explain where development stands: the domain primitives and schedule generators are in strong shape, extension by composition is already possible, and the remaining gap is higher-level integrated metrics such as first-class IRR/LCOE workflows.
