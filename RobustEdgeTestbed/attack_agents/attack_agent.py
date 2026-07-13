"""
   Abstract class for attack agents

   Author: Thanikesavan Sivanthi
"""
from abc import ABC, abstractmethod

class AttackAgent(ABC):
   """This is an abstract class for all attack agents
   """
   @abstractmethod
   def startAttack(self):
      """Abstarct method to start the attack
      """
      pass
     
   @abstractmethod
   def stopAttack(self):
      """Abstarct method to stop the attack
      """
      pass