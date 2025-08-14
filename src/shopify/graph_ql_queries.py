#


RETURN_ORDERS_QUERY = """
query ($first: Int, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query) {
    pageInfo {
      hasNextPage
      hasPreviousPage
      startCursor
      endCursor
    }
    edges {
      cursor
      node {
        id
        name
        tags
        transactions {
          id
          kind
          gateway
          amountSet {
            presentmentMoney {
              amount
            }
            shopMoney {
              amount
              currencyCode
            }
          }
        }
        suggestedRefund(
          suggestFullRefund: true
          refundMethodAllocation: ORIGINAL_PAYMENT_METHODS
          refundShipping: true
        ) {
          amountSet {
            presentmentMoney {
              amount
              currencyCode
            }
          }
          refundDuties {
            amountSet {
              presentmentMoney {
                amount
                currencyCode
              }
            }
            originalDuty {
              id
            }
          }
          shipping {
            amountSet {
              presentmentMoney {
                amount
                currencyCode
              }
            }
          }
          suggestedTransactions {
            amountSet {
              presentmentMoney {
                amount
                currencyCode
              }
            }
            gateway
            kind
            parentTransaction {
              id
            }
          }
        }
        totalPriceSet {
          shopMoney {
            amount
            currencyCode
          }
          presentmentMoney {
            amount
            currencyCode
          }
        }
        lineItems(first: $first) {
          nodes {
            id
            quantity
            refundableQuantity
            originalTotalSet {
              presentmentMoney {
                amount
                currencyCode
              }
              shopMoney {
                amount
                currencyCode
              }
            }
            discountAllocations {
              allocatedAmountSet {
                presentmentMoney {
                  amount
                  currencyCode
                }
                shopMoney {
                  amount
                  currencyCode
                }
              }
            }
            taxLines {
              title
              rate
              priceSet {
                presentmentMoney {
                  amount
                  currencyCode
                }
                shopMoney {
                  amount
                  currencyCode
                }
              }
            }
          }
        }
        fulfillments {
          id
          name
          totalQuantity
          displayStatus
          requiresShipping
          trackingInfo(first: 10) {
            number
            company
            url
          }
        }
        returns(first: $first) {
          nodes {
            id
            name
            status
            returnLineItems(first: 10) {
              nodes {
                ... on ReturnLineItem {
                  id
                  quantity
                  returnReason
                  returnReasonNote
                  fulfillmentLineItem {
                    id
                    lineItem {
                      id
                      name
                      quantity
                      originalTotalSet {
                        presentmentMoney {
                          amount
                          currencyCode
                        }
                        shopMoney {
                          amount
                          currencyCode
                        }
                      }
                    }
                  }
                }
              }
            }
            reverseFulfillmentOrders(first: 5) {
              nodes {
                reverseDeliveries(first: 5) {
                  nodes {
                    deliverable {
                      ... on ReverseDeliveryShippingDeliverable {
                        tracking {
                          carrierName
                          number
                          url
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


REFUND_CREATE_MUTATION = """
mutation RefundLineItem($input: RefundInput!) {
  refundCreate(input: $input) {
    refund { 
      id
      note
      createdAt
      totalRefundedSet { 
          presentmentMoney { 
              amount
              currencyCode
          }
      }
    }
    userErrors { 
      field
      message
    }
  }
}
"""
