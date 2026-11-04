// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.38;
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
/// @dev Private-EVM test token. Not USDC and not for deployment.
contract TestDollar is ERC20 {
    constructor(address holder, uint256 amount) ERC20("Private EVM Test Dollar", "TEST") { _mint(holder, amount); }
    function decimals() public pure override returns (uint8) { return 6; }
}
