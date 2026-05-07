## Category: Security (SEC)
Issues related to security.

### SEC001: Insecure tool design - OWASP-LLM007: Insecure plugin design
**Severity**: Critical 

**Description**: The tool relies on the Agent (LLM) to input parameters to the tool, such as account numbers, that could return sensitive data of other customers by manipulating inputs. A malicious attacker can trick the LLM into inputting other values that could compromise the system.

**Remediation**: Design the tool such that it validates all inputs and does not rely on the Agent (LLM) to input parameters to the tool, such as account numbers, that could return sensitive data of other customers by manipulating inputs.  

#### Common Examples of Vulnerability
- A plugin accepts all parameters in a single text field instead of distinct input parameters.
- A plugin accepts configuration strings, instead of parameters, that can override entire configuration settings.
- A plugin accepts raw SQL or programming statements instead of parameters.
- Authentication is performed without explicit authorization to a particular plugin.
- A plugin treats all LLM content as being created entirely by the user and performs any requested actions without requiring additional authorization.
