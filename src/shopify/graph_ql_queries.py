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
        disputes {
          status
          initiatedAs
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
          presentmentMoney {
            amount
            currencyCode
          }
        }
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
        discountApplications(first: 5) {
          edges {
            node {
              allocationMethod
              targetSelection
              targetType
            }
          }
        }
        totalRefundedShippingSet {
          presentmentMoney {
            amount
            currencyCode
          }
        }
        lineItems(first: $first) {
          nodes {
            id
            quantity
            isGiftCard
            refundableQuantity
            originalTotalSet {
              presentmentMoney {
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
              }
            }
          }
        }
        fulfillments {
          id
          name
          requiresShipping
          trackingInfo(first: 5) {
            number
            company
            url
          }
        }
        returns(first: 10, query: "status:OPEN") {
          nodes {
            id
            name
            status
            returnLineItems(first: 5) {
              nodes {
                ... on ReturnLineItem {
                  id
                  quantity
                  refundableQuantity
                  fulfillmentLineItem {
                    id
                    lineItem {
                      id
                      name
                      quantity
                      isGiftCard
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
        refunds(first: 10) {
          createdAt
          totalRefundedSet {
            presentmentMoney {
              amount
              currencyCode
            }
          }
          refundLineItems(first: 5) {
            nodes {
              lineItem {
                id
                isGiftCard
              }
              quantity
            }
          }
        }
      }
    }
  }
}
"""

RETURN_CLOSE_MUTATION = """
mutation RefundLineItem($returnId: ID!) {
  returnClose(id: $returnId) {
    return {
      id
      status
      closedAt
    }
    userErrors {
      code
      field
      message
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
