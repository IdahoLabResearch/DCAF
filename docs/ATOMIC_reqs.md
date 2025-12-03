
# DCF Analysis Python Library (name pending)
Software Requirements Drafting

## Acronym Brainstorming
- ATOMIC is a Tool for Optimized Management of Investment Calculation (ATOMIC)
- DCF Analysis Python Library (DAPL)


## Financial Analysis
### Metrics
The Software shall perform calculation of the following metrics, which are based on discount cash
flow models:
- Net Present Value (NPV)
- Levelized Cost (LC), specifically Levelized Cost of Electricity (LCOE)
- Internal Rate of Return (IRR)

In addition, the Software will provide the following undiscounted metrics:
- Sum of Cashflows
- Undiscounted Levelized Cost

### Inflation, Tax, and Depreciation
The Software will provide the ability to use either constant or custom user-input inflation schedule.

The Software will provide the ability to use either constant or custom user-input tax schedule.

The Software will provide the following methods to provide discount rate:
- User-provided value
- [future] Debt-to-equity ratio, return on debt, cost of equity, and tax rate

### Outputs
The Software will provide the following outputs as a result of analysis:
- Each of the Metrics listed above
- Pro Forma: summary of cashflows in income statement format similar to financial analysis standars for pro forma
- Cashflow plots, including individual and project cashflow plots.
- [future] allow passing algebraic variables through to create expressions for use in optimization algorithms

### Other Features
- The Software will be able to [de]escalate cash flows to a user-provided start year.
- The Software will be able to calculate metrics based on user-provided project start year and either project length or end year.



### Resolution
- The Software will calculate cashflows on a yearly resolution.
- The Software will allow the user to provide schedules to enable and disable each cashflow for arbitrary years of the project life.


## Cashflows
The Software will allow for the following types of cashflows to be included in the analysis:
### Traditional Cash Flows
- Captial Expenditure (capex): single-time purchase of assets.
- Operations and Maintenance (O&M)
  - Fixed O&M
  - Variable O&M that depends on unit operation
- Fuel Costs
- Financing
- Revenue

### Tax Incentive Cash Flows
- Production Tax Credit (PTC): per-kW incentive for generation
- Investment Tax Credit (ITC): one-time incentive payed when generation starts
- Milestone Progress Incentive: grants or similar payments provided at an arbitrary schedule as certain milestones of construction are met
- Tax Holiday Incentive: delay of interest accrual during construction period
- MACRS depreciation with common schedules: 5, 10, 15, and 20 years, as well as custom user-defined schedule
- Custom incentives based on user-provided parameters. These will flexibly allow the user to determine
  - Taxed or untaxed
  - Schedule of payment
  - Recurring or one-time

### Format
The Software will allow flexible definition of cash flow forms using a piecewise linear combination of the following types of:
- f(D) = a (D/D')^x
- polynomial expressions
- [future] allow multiplying expressions

## Software Architecture

### Software Deployment
The Software will be deployed through the Python PIP distribution system.

### Dependency Maintenance
The Software's library dependencies will be maintained through a standard Python library management system.

### Syntax Standards
Syntax standards will be enforced through common Python syntax enforcing measures.

### SQA
The Software will be maintained on the principles of NQA-1 level 3:
- Software documentation
- Regression testing
- Peer Review code changes

## Principles of Development
- Minimize the number of dependent libraries.

## Won't Do
The Software will not provide values for cashflow inputs; these will be provided by the user. A seperate database of values may be maintained independently of this software.
