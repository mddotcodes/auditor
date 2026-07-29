# Weird ERC20 subset

Minimal ports inspired by [d-xo/weird-erc20](https://github.com/d-xo/weird-erc20):

- `MissingReturns.sol` — no bool returns  
- `TransferFee.sol` — fee-on-transfer  
- `ReentrantToken.sol` — callback on transfer  

Primary assert: **compiles offline** with default vendor remappings. Not a full token-integration suite.
